"""Whoosh'd FastAPI application.

Multi-runtime inference broker for Apple Silicon.

Endpoints:
  * /health — liveness probe
  * /health/runtime — per-runtime health snapshot
  * /ready — readiness probe
  * /runtime — full runtime snapshot
  * /v1/models — OpenAI-compatible model inventory (aggregated)
  * /api/tags — Ollama-compatible model tags (aggregated)
  * /v1/chat/completions — OpenAI-compatible chat (routed)
  * /v1/generate — Codexify-style generation (routed)

Runtime support:
  * llama_cpp — llama.cpp server (GGUF models)
  * mlx_lm_server — mlx_lm.server (MLX models)
  * mlx_lm — in-process MLX (legacy)
  * stub — deterministic test adapter
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from whooshd import __version__
from whooshd.admission import evaluate_chat_request
from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorCode,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    ModelLifecycleState,
    ModelsResponse,
    ReadinessResponse,
    RequestExecutionContext,
    RequestLifecycleState,
    RunnerStatus,
    RuntimeKind,
)
from whooshd.config import (
    get_adapter_backend,
    get_advertised_model_id,
    get_mlx_lm_server_enabled,
    get_mlx_vlm_enabled,
)
from whooshd.routing import ModelResolutionError, get_router, reset_router
from whooshd.runtime import get_runtime
from whooshd.http_forwarding import UpstreamRuntimeError
from whooshd.runtime.threadwake import ThreadWakeManager


logger = logging.getLogger(__name__)
_threadwake_manager = ThreadWakeManager()

app = FastAPI(
    title="Whoosh'd",
    description="Memory-aware local inference broker for Apple Silicon",
    version=__version__,
)


# ── Router setup ──────────────────────────────────────────────────────────


def _init_router():
    """Register all configured runtime adapters with the router.

    Called once at module load.  The router is shared across all
    request handlers.
    """
    router = get_router()
    backend = get_adapter_backend()

    # ── Stub adapter (always registered as fallback) ────────────
    from whooshd.adapters.stub import StubInferenceAdapter
    router.register(StubInferenceAdapter())

    # ── MLX-LM Server adapter (subprocess-supervised) ──────────
    if get_mlx_lm_server_enabled():
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter
        router.register(MlxLmServerAdapter())

    # ── MLX in-process adapter (legacy, when WHOOSHD_ADAPTER=mlx)
    if backend == "mlx":
        from whooshd.adapters.mlx import MLXInferenceAdapter
        router.register(MLXInferenceAdapter())

    # ── MLX-VLM adapter (vision-language, subprocess-supervised) ──
    if get_mlx_vlm_enabled():
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter
        router.register(MlxVlmAdapter())

    # ── llama.cpp adapter ──────────────────────────────────────
    # Register when server URL is configured OR adapter is explicitly selected.
    from whooshd.config import get_llama_cpp_server_url
    if backend == "llama_cpp" or get_llama_cpp_server_url():
        from whooshd.adapters.llama_cpp import LlamaCppAdapter
        router.register(LlamaCppAdapter())


_init_router()


# ── Health ──────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Runner liveness and basic state probe."""
    rt = get_runtime()
    return HealthResponse(
        ok=True,
        runner="whooshd",
        version=__version__,
        status=rt.status,
        model_lifecycle=rt.model_lifecycle,
        active_model=rt.active_model,
        queue_depth=rt.queue_depth,
        active_jobs=rt.active_jobs,
        memory=rt.memory,
    )


# ── Per-runtime health ─────────────────────────────────────────────────────


@app.get("/health/runtime")
async def health_runtime():
    """Per-runtime health snapshot.

    Returns the state of every registered runtime backend
    so orchestrators can distinguish process availability
    from model readiness per runtime lane.
    """
    router = get_router()
    return await router.health()


# ── Readiness ──────────────────────────────────────────────────────────────


@app.get("/ready")
async def ready():
    """Readiness probe — can this provider accept inference right now?

    Returns 200 when at least one non-stub runtime is ready, or the global
    lifecycle reports READY.  Returns 503 when no runtime can serve inference.

    The stub adapter is excluded from readiness decisions — it exists
    for testing and should not cause a false "ready".
    """
    rt = get_runtime()
    router = get_router()
    configured = get_advertised_model_id()

    lifecycle = rt.model_lifecycle
    is_ready = lifecycle == ModelLifecycleState.READY

    # If the global lifecycle says not ready, check if any non-stub
    # adapter reports ready via per-runtime health.
    if not is_ready:
        try:
            multi_health = await router.health()
            for kind, h in multi_health.runtimes.items():
                if kind == "stub":
                    continue  # stub is always ready — exclude it
                if h.state.value == "ready":
                    is_ready = True
                    break
        except Exception:
            pass

    # Determine reason and HTTP status.
    reason: Optional[str] = None
    if is_ready:
        reason = None
    elif lifecycle == ModelLifecycleState.WARMING:
        reason = "model_warming"
    elif lifecycle == ModelLifecycleState.UNLOADED:
        reason = "model_unloaded"
    elif lifecycle == ModelLifecycleState.FAILED:
        reason = "model_load_failed"
    elif lifecycle == ModelLifecycleState.DEGRADED:
        reason = "model_degraded"

    body = ReadinessResponse(
        ready=is_ready,
        status=rt.status,
        model_lifecycle=lifecycle,
        adapter="multi-runtime",
        configured_model=configured,
        loaded_model=None,
        reason=reason,
    )

    status_code = 200 if is_ready else 503
    return JSONResponse(content=body.model_dump(), status_code=status_code)


# ── Runtime ────────────────────────────────────────────────────────────────


@app.get("/runtime")
async def runtime():
    """Full runtime snapshot: memory, loaded models, concurrency budget."""
    rt = get_runtime()
    return rt.build_runtime_response()


# ── Models ─────────────────────────────────────────────────────────────────


@app.get("/models", response_model=ModelsResponse)
async def models() -> ModelsResponse:
    """List registered models and their load state.

    Aggregated across all registered runtime backends.
    """
    rt = get_runtime()
    return ModelsResponse(models=await rt.list_models_async())


# ── Startup: sync lifecycle with router reality ────────────────────────────


def _init_lifecycle():
    """If any adapter reports loaded, mark the runtime lifecycle as ready."""
    rt = get_runtime()
    router = get_router()
    for adapter in router._adapters.values():
        if adapter.is_loaded():
            rt.complete_warmup()
            break


_init_lifecycle()


# ── Helper: map HTTP status to ErrorCode ──────────────────────────────────


def _error_code_for_status(status: int) -> ErrorCode:
    if status == 404:
        return ErrorCode.MODEL_NOT_FOUND
    elif status in (408, 504):
        return ErrorCode.TIMEOUT
    elif status == 429:
        return ErrorCode.RUNNER_OVERLOADED
    elif status == 503:
        return ErrorCode.INTERNAL
    return ErrorCode.INTERNAL


# ── Vision capability helpers ─────────────────────────────────────────────


def _request_has_image_content(req: ChatCompletionRequest) -> bool:
    """Return True if any message in the request contains image content.

    Detects OpenAI-compatible multimodal message format where content
    is a list and any part has ``type: image_url``.
    """
    for msg in req.messages:
        content = getattr(msg, "content", None)
        if content is None:
            continue
        # String content — no images.
        if isinstance(content, str):
            continue
        # List content — multimodal.  Check for image parts.
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _adapter_supports_vision(adapter) -> bool:
    """Return True if the adapter is known to support vision/image input."""
    # Check the adapter's kind.
    kind = getattr(adapter, "kind", "")
    if kind == RuntimeKind.MLX_VLM.value:
        return True
    # Check list_models output for supports_vision flag.
    return False


# ── Validation error handler ───────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            code=ErrorCode.INTERNAL,
            message="Validation error",
            detail={"errors": exc.errors()},
        ).model_dump(),
    )


# ── Model resolution error handler ─────────────────────────────────────────

@app.exception_handler(ModelResolutionError)
async def _model_resolution_error_handler(request, exc: ModelResolutionError):
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            code=ErrorCode.MODEL_NOT_FOUND,
            message=str(exc),
            detail={"model_id": exc.model_id},
        ).model_dump(),
    )


# ── Upstream runtime error handler ─────────────────────────────────────────

@app.exception_handler(UpstreamRuntimeError)
async def _upstream_runtime_error_handler(request, exc: UpstreamRuntimeError):
    """Classify upstream runtime errors into appropriate HTTP responses.

    Upstream errors carry their own http_status so the HTTP layer
    does not need conditionals per error type.
    """
    # Map http_status to ErrorCode for the response body.
    error_code: ErrorCode
    if exc.http_status == 404:
        error_code = ErrorCode.MODEL_NOT_FOUND
    elif exc.http_status in (408, 504):
        error_code = ErrorCode.TIMEOUT
    elif exc.http_status == 425:
        error_code = ErrorCode.MODEL_LOAD_FAILED  # warming
    elif exc.http_status == 429:
        error_code = ErrorCode.RUNNER_OVERLOADED
    elif exc.http_status == 503:
        error_code = ErrorCode.INTERNAL  # unavailable
    else:
        error_code = ErrorCode.INTERNAL

    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            code=error_code,
            message=str(exc),
            detail={"kind": type(exc).__name__},
        ).model_dump(),
    )


# ── Generate ───────────────────────────────────────────────────────────────


@app.post("/v1/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Generate text from a prompt using the correct runtime adapter.

    Routed based on model_id → runtime resolution.
    """
    rt = get_runtime()
    router = get_router()

    # ── Admission control (simple: just active request limit) ─────────
    from whooshd.config import get_max_active_requests

    if rt.active_jobs >= get_max_active_requests():
        rt.record_rejected("rejected_overloaded")
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(
                code=ErrorCode.RUNNER_OVERLOADED,
                message=f"Whoosh'd is at its active request limit.",
                detail={"active_jobs": rt.active_jobs},
            ).model_dump(),
        )

    rt.record_accepted()

    model = req.model_id or "stub-model"
    request_id = rt.begin_request(model=model, stream=False)
    rt.mark_running(request_id)

    try:
        result = await router.generate(req)
        rt.complete_request(request_id)
        return result
    except Exception as exc:
        rt.fail_request(request_id)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCode.INTERNAL,
                message="Inference failed",
                detail={"error": str(exc)},
            ).model_dump(),
        ) from exc


# ── Streaming not-supported handler ────────────────────────────────────────

@app.exception_handler(StreamingNotSupportedError)
async def _streaming_not_supported_handler(request, exc: StreamingNotSupportedError):
    return JSONResponse(
        status_code=501,
        content=ErrorResponse(
            code=ErrorCode.INTERNAL,
            message=str(exc),
            detail={"hint": "Set stream=false or use an adapter that supports streaming."},
        ).model_dump(),
    )


# ── OpenAI-compatible Chat Completions ─────────────────────────────────────


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """OpenAI-compatible chat completion endpoint.

    - stream=false → JSON response with the full completion.
    - stream=true  → SSE text/event-stream with OpenAI-compatible chunks.

    Requests are routed to the correct runtime based on model ID.
    """
    rt = get_runtime()
    router = get_router()

    # ── Admission control ─────────────────────────────────────────────
    admission = evaluate_chat_request(req, rt)
    if not admission.accepted:
        rt.record_rejected(admission.reason.value)
        return JSONResponse(
            status_code=admission.http_status,
            content=ErrorResponse(
                code=admission.error_code or ErrorCode.INTERNAL,
                message=admission.message or "Request rejected.",
                detail=admission.details,
            ).model_dump(),
        )

    rt.record_accepted()

    # ── Resolve the adapter via router ───────────────────────────────
    try:
        adapter = await router._resolve_model_runtime(req.model)
    except ModelResolutionError as exc:
        rt.record_rejected("rejected_model_not_ready")
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                code=ErrorCode.MODEL_NOT_FOUND,
                message=str(exc),
                detail={"model_id": exc.model_id},
            ).model_dump(),
        )

    # ── Vision capability check ──────────────────────────────────────
    has_image = _request_has_image_content(req)
    if has_image:
        # Check if the resolved adapter supports vision.
        adapter_supports_vision = _adapter_supports_vision(adapter)
        if not adapter_supports_vision:
            rt.record_rejected("rejected_model_not_ready")
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    code=ErrorCode.INTERNAL,
                    message=f"Model '{req.model}' does not support vision/image input. "
                            f"Use a vision-capable model (mlx-vlm).",
                    detail={"model_id": req.model, "has_image": True},
                ).model_dump(),
            )

    # ── ThreadWake observe-mode metadata ──────────────────────────────
    threadwake_observation = _threadwake_manager.observe_request(
        req,
        backend=getattr(adapter, "kind", None),
    )
    if threadwake_observation.enabled:
        logger.info(
            "threadwake.observe mode=%s eligible=%s reason=%s "
            "stable_prefix_hash=%s stable_prefix_tokens=%s dynamic_tokens=%s "
            "estimated_prefill_reuse_tokens=%s cache_hit=%s cache_scope=%s",
            threadwake_observation.mode.value,
            threadwake_observation.eligible,
            threadwake_observation.reason,
            threadwake_observation.stable_prefix_hash,
            threadwake_observation.stable_prefix_tokens,
            threadwake_observation.dynamic_tokens,
            threadwake_observation.estimated_prefill_reuse_tokens,
            threadwake_observation.cache_hit,
            threadwake_observation.cache_scope,
        )

    # ── Non-streaming path ────────────────────────────────────────────
    if not req.stream:
        request_id = rt.begin_request(model=req.model, stream=False)
        rt.mark_running(request_id)
        token = rt.get_cancellation_token(request_id)
        ctx = RequestExecutionContext(
            request_id=request_id, cancellation_token=token, stream=False
        ) if token else None
        try:
            result = await adapter.chat_completion(req, context=ctx)
            rt.complete_request(request_id)
            return result
        except UpstreamRuntimeError:
            rt.fail_request(request_id)
            raise
        except Exception:
            rt.fail_request(request_id)
            raise

    # ── Streaming path ────────────────────────────────────────────────
    if not adapter.supports_streaming:
        raise StreamingNotSupportedError()

    request_id = rt.begin_request(model=req.model, stream=True)
    rt.mark_streaming(request_id)
    token = rt.get_cancellation_token(request_id)
    ctx = RequestExecutionContext(
        request_id=request_id, cancellation_token=token, stream=True
    ) if token else None

    # Eagerly get the first chunk so overload errors surface as 429
    # before the SSE stream starts.
    stream_gen = adapter.chat_completion_stream(req, context=ctx)
    try:
        first_chunk = await stream_gen.__anext__()
    except StopAsyncIteration:
        # Empty stream — still return SSE with just [DONE].
        async def _empty_sse():
            yield "data: [DONE]\n\n"
        rt.complete_request(request_id)
        return StreamingResponse(
            _empty_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    except UpstreamRuntimeError as exc:
        rt.fail_request(request_id)
        return JSONResponse(
            status_code=exc.http_status,
            content=ErrorResponse(
                code=_error_code_for_status(exc.http_status),
                message=str(exc),
                detail={"kind": type(exc).__name__},
            ).model_dump(),
        )
    except Exception:
        rt.fail_request(request_id)
        raise

    async def _sse_stream():
        finished_normally = False
        try:
            # Yield the eagerly-fetched first chunk.
            yield first_chunk.to_sse()
            # Continue with remaining chunks.
            async for chunk in stream_gen:
                yield chunk.to_sse()
            yield "data: [DONE]\n\n"
            finished_normally = True
        except UpstreamRuntimeError as exc:
            error_json = json.dumps({
                "error": {
                    "message": str(exc),
                    "type": type(exc).__name__,
                }
            })
            yield f"data: {error_json}\n\n"
        finally:
            if finished_normally:
                rt.complete_request(request_id)
            else:
                rt.record_stream_disconnect(request_id)
                rt.cancel_request(request_id)

    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── OpenAI-compatible Model Inventory ──────────────────────────────────────


@app.get("/v1/models")
async def openai_models():
    """OpenAI-compatible model list — aggregated across all runtimes.

    When a model registry is configured, registry entries provide
    the authoritative metadata.  When no registry is present, each
    runtime adapter contributes its own model list.
    """
    rt = get_runtime()
    return await rt.build_openai_model_list()


# ── Ollama-compatible Tags (model inventory alias) ─────────────────────────


@app.get("/api/tags")
async def ollama_tags():
    """Ollama-compatible model tags list — aggregated across all runtimes."""
    rt = get_runtime()
    return await rt.build_ollama_tags()


# ── Internal runtime: request lifecycle ────────────────────────────────────


@app.get("/runtime/requests")
async def runtime_requests():
    """List all tracked requests and their lifecycle states.

    Internal/debug endpoint — not part of the OpenAI-compatible API.
    """
    rt = get_runtime()
    return rt.build_request_list()


@app.post("/runtime/requests/{request_id}/cancel")
async def runtime_cancel_request(request_id: str):
    """Cancel an active request by ID.

    Internal/debug endpoint — not part of the OpenAI-compatible API.
    Signals the request's cancellation token so cooperative adapters
    stop generating.  Returns 404 if unknown, 409 if already terminal.
    """
    rt = get_runtime()
    snap = rt.get_request_snapshot(request_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCode.INTERNAL,
                message=f"Request {request_id} not found",
            ).model_dump(),
        )
    if snap.status in (
        RequestLifecycleState.COMPLETED,
        RequestLifecycleState.CANCELLED,
        RequestLifecycleState.FAILED,
    ):
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCode.INTERNAL,
                message=f"Request {request_id} is already in terminal state {snap.status.value}",
            ).model_dump(),
        )
    signalled = rt.request_cancellation(request_id)
    if signalled:
        rt.cancel_request(request_id)
    return {
        "ok": True,
        "request_id": request_id,
        "cancelled": signalled,
        "status": "cancelled",
    }


# ── Internal runtime: model lifecycle ──────────────────────────────────────


@app.get("/runtime/model")
async def runtime_model():
    """Current model lifecycle snapshot.

    Returns adapter name, configured model, loaded model, lifecycle state,
    and timing/error metadata.  No prompts, messages, or generated text.
    """
    rt = get_runtime()
    router = get_router()
    configured = get_advertised_model_id()
    # Use the first non-stub adapter for the snapshot, or stub as fallback.
    adapter_name = "multi-runtime"
    for kind in router.registered_kinds:
        if kind != "stub":
            adapter = router.get_adapter(kind)
            if adapter:
                adapter_name = adapter.name
                break
    return rt.build_model_snapshot(
        adapter_name=adapter_name, configured_model=configured
    )


@app.post("/runtime/model/warmup")
async def runtime_model_warmup():
    """Trigger model warmup / loading on all registered runtimes."""
    rt = get_runtime()
    router = get_router()

    rt.begin_warmup()
    try:
        results = await router.warmup_all()
        rt.complete_warmup()
    except Exception as exc:
        rt.fail_warmup(
            error_code="MODEL_LOAD_FAILED",
            error_message=str(exc)[:256],
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCode.MODEL_LOAD_FAILED,
                message=f"Model warmup failed: {exc}",
            ).model_dump(),
        ) from exc

    configured = get_advertised_model_id()
    return rt.build_model_snapshot(
        adapter_name="multi-runtime", configured_model=configured
    )


@app.post("/runtime/model/unload")
async def runtime_model_unload():
    """Unload models from all registered runtimes.

    Returns 409 Conflict if active requests are running.
    """
    rt = get_runtime()
    router = get_router()

    if rt.active_jobs > 0:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCode.INTERNAL,
                message=f"Cannot unload: {rt.active_jobs} active request(s) in progress.",
            ).model_dump(),
        )

    await router.unload_all()
    rt.complete_unload()

    configured = get_advertised_model_id()
    return rt.build_model_snapshot(
        adapter_name="multi-runtime", configured_model=configured
    )


# ── Internal runtime: admission config ─────────────────────────────────────


@app.get("/runtime/admission")
async def runtime_admission():
    """Current admission limits and counters."""
    rt = get_runtime()
    return rt.build_admission_config()


# ── ThreadWake health ──────────────────────────────────────────────────────


@app.get("/health/threadwake")
async def health_threadwake():
    """ThreadWake cache health and metrics.

    Returns enabled status, mode, entry counts, memory estimates,
    hit/miss/eviction counters, and backend capability summary.
    No raw prompt content or opaque KV refs are exposed.
    """
    return _threadwake_manager.get_health()


# ── ThreadWake admin flush ─────────────────────────────────────────────────


@app.post("/runtime/threadwake/flush")
async def runtime_threadwake_flush(req: dict | None = None):
    """Flush ThreadWake cache metadata entries.

    Request body (optional):
      {"scope": "all"|"thread"|"project"|"user"|"global",
       "scope_id": "...",
       "model_id": "..."}

    Omit the body to flush all entries.
    """
    body = req or {}
    scope = body.get("scope")
    # "all" means flush everything (scope=None)
    if scope == "all":
        scope = None
    model_id = body.get("model_id")
    scope_id = body.get("scope_id")

    result = _threadwake_manager.flush_cache(
        scope=scope,
        model_id=model_id,
        scope_id=scope_id,
    )
    # Record evictions in metrics
    _threadwake_manager.metrics.record_eviction(result["flushed"])
    return result

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
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from whooshd import __version__
from whooshd.admission import AdmissionDecision, evaluate_chat_request
from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.backend_request_policy import (
    BackendRequestPolicyError,
    ensure_backend_chat_request,
)
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
    WHOOSHD_RUNTIME_PROVENANCE_HEADER,
)
from whooshd.control_plane import (
    CONTROL_PLANE_CONTRACT_VERSION,
    CONTROL_PLANE_VERSION_HEADER,
    LEGACY_CONTROL_PLANE_VERSION_HEADER,
    TARGET_CONTRACT_VERSION_HEADER,
    ErrorCode as ControlErrorCode,
    code_for_http_status,
    error_fields,
    negotiate_contract_version,
)
from whooshd.config import (
    get_adapter_backend,
    get_advertised_model_id,
    get_mlx_lm_server_enabled,
    get_mlx_vlm_enabled,
)
from whooshd.correlation import (
    UPSTREAM_REQUEST_ID_HEADER,
    correlation_response_headers,
    normalize_identifier,
)
from whooshd.routing import (
    ModelResolutionError,
    RuntimeResolution,
    get_router,
    reset_router,
)
from whooshd.runtime import get_runtime
from whooshd.queue import QueueEntry, get_queue
from whooshd.http_forwarding import UpstreamRuntimeError
from whooshd.log_safety import (
    exception_metadata,
    failure_class,
    safe_exception_message,
    safe_model_alias,
)
from whooshd.runtime.threadwake import ThreadWakeManager
from whooshd.runtime.threadwake.tokenization import BackendTokenizerAdapterRegistry
from whooshd.runtime.threadwake.analysis_loop import ThreadWakeAnalysisLoop


logger = logging.getLogger(__name__)
_tokenizer_registry = BackendTokenizerAdapterRegistry()
_threadwake_manager = ThreadWakeManager(tokenizer_registry=_tokenizer_registry)
_threadwake_analysis_loop: ThreadWakeAnalysisLoop | None = None


def _safe_upstream_error_detail(exc: UpstreamRuntimeError) -> dict:
    """Keep only bounded upstream failure metadata in outward diagnostics."""
    detail = {
        "kind": type(exc).__name__,
        "failure_class": failure_class(exc),
    }
    allowed = {
        "upstream_status",
        "http_status",
        "body_bytes",
        "body_present",
        "content_type",
        "endpoint",
        "timeout_seconds",
    }
    raw_detail = getattr(exc, "detail", None)
    if isinstance(raw_detail, dict):
        detail.update({key: raw_detail[key] for key in allowed if key in raw_detail})
    return detail


def _get_analysis_loop() -> ThreadWakeAnalysisLoop:
    global _threadwake_analysis_loop
    if _threadwake_analysis_loop is None:
        _threadwake_analysis_loop = ThreadWakeAnalysisLoop(
            index=_threadwake_manager._index,
        )
    return _threadwake_analysis_loop

app = FastAPI(
    title="Whoosh'd",
    description="Memory-aware local inference broker for Apple Silicon",
    version=__version__,
)


def _upstream_request_id(request: Request) -> str | None:
    """Return the accepted upstream identity associated with this request."""

    return normalize_identifier(getattr(request.state, "upstream_request_id", None))


def _whoosh_request_id(request: Request) -> str | None:
    """Return the Whoosh'd-owned lifecycle identity, if one exists."""

    return normalize_identifier(getattr(request.state, "whoosh_request_id", None))


def _apply_correlation_headers(response, request: Request) -> None:
    """Attach only validated, distinct correlation headers to a response."""

    response.headers.update(
        correlation_response_headers(
            upstream_request_id=_upstream_request_id(request),
            whoosh_request_id=_whoosh_request_id(request),
        )
    )


@app.middleware("http")
async def add_control_plane_version_header(request, call_next):
    """Advertise the response contract on every Whoosh'd-owned API response."""
    request.state.upstream_request_id = normalize_identifier(
        request.headers.get(UPSTREAM_REQUEST_ID_HEADER)
    )
    negotiation = negotiate_contract_version(
        target_value=request.headers.get(TARGET_CONTRACT_VERSION_HEADER),
        legacy_value=request.headers.get(LEGACY_CONTROL_PLANE_VERSION_HEADER),
    )
    if not negotiation.compatible:
        response = JSONResponse(
            status_code=400,
            content=_error_body(
                ControlErrorCode.CONTRACT_VERSION_UNSUPPORTED,
                "Unsupported control-plane contract version",
                http_status=400,
                upstream_request_id=_upstream_request_id(request),
                details={"received_version": negotiation.incompatibility},
            ),
        )
        response.headers[CONTROL_PLANE_VERSION_HEADER] = CONTROL_PLANE_CONTRACT_VERSION
        _apply_correlation_headers(response, request)
        return response
    response = await call_next(request)
    response.headers[CONTROL_PLANE_VERSION_HEADER] = CONTROL_PLANE_CONTRACT_VERSION
    _apply_correlation_headers(response, request)

    # Retry-After is deliberately derived from the already-bounded response
    # envelope.  Streaming bodies are not inspected.
    raw_body = getattr(response, "body", None)
    if raw_body:
        try:
            payload = json.loads(raw_body)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            envelope = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            retry_after = envelope.get("retry_after_seconds") if isinstance(envelope, dict) else None
            if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
                response.headers["Retry-After"] = str(retry_after).rstrip("0").rstrip(".")
    return response


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
        router.register(MLXInferenceAdapter(tokenizer_registry=_tokenizer_registry, kv_backend_registry=_threadwake_manager._backend_registry))

    # ── MLX-VLM adapter (vision-language, subprocess-supervised) ──
    if get_mlx_vlm_enabled():
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter
        router.register(MlxVlmAdapter())

    # ── llama.cpp adapter (always registered — reports offline until
    #     WHOOSHD_LLAMA_CPP_SERVER_URL is set or auto_start is enabled) ─
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
    """Mark ready when a configured adapter can serve.

    The deterministic stub is intentionally considered ready when it is the
    only configured adapter, because that is the supported no-model test
    runtime. Deployments with real adapters use the warmup result gate above;
    stub readiness must not mask a configured real-runtime failure.
    """
    rt = get_runtime()
    router = get_router()
    for adapter in router._adapters.values():
        if adapter.is_loaded():
            rt.complete_warmup()
            break


_init_lifecycle()


# ── Canonical error response helpers ─────────────────────────────────────


def _error_code_for_status(status: int) -> ErrorCode:
    return ErrorCode(code_for_http_status(status).value)


def _error_body(
    code: ErrorCode | ControlErrorCode | str,
    message: str,
    *,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
    request_id: str | None = None,
    upstream_request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical, JSON-ready error envelope."""
    return ErrorResponse(
        **error_fields(
            code.value if isinstance(code, (ErrorCode, ControlErrorCode)) else code,
            message=message,
            http_status=http_status,
            retry_after_seconds=retry_after_seconds,
            request_id=request_id,
            upstream_request_id=upstream_request_id,
            details=details,
        )
    ).model_dump(mode="json", exclude_none=True)


def _error_body_for_request(
    request: Request,
    code: ErrorCode | ControlErrorCode | str,
    message: str,
    *,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical error body from only validated request metadata."""

    return _error_body(
        code,
        message,
        http_status=http_status,
        retry_after_seconds=retry_after_seconds,
        request_id=request_id if request_id is not None else _whoosh_request_id(request),
        upstream_request_id=_upstream_request_id(request),
        details=details,
    )


def _begin_request_lifecycle(
    rt,
    request: Request,
    *,
    model: str,
    stream: bool,
) -> str:
    """Create and retain a Whoosh'd lifecycle identity for a request."""

    request_id = rt.begin_request(
        model=model,
        stream=stream,
        upstream_request_id=_upstream_request_id(request),
    )
    request.state.whoosh_request_id = request_id
    return request_id


def _execution_context(
    request: Request,
    *,
    request_id: str,
    cancellation_token,
    stream: bool,
) -> RequestExecutionContext | None:
    """Pass the local ID to adapters while retaining upstream metadata apart."""

    if cancellation_token is None:
        return None
    return RequestExecutionContext(
        request_id=request_id,
        cancellation_token=cancellation_token,
        stream=stream,
        upstream_request_id=_upstream_request_id(request),
    )


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
        content=_error_body_for_request(
            request,
            ErrorCode.INVALID_REQUEST,
            "Request validation failed",
            http_status=422,
            details={"failure_class": "validation", "error_count": len(exc.errors())},
        ),
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request, exc: HTTPException):
    """Normalize route-raised HTTP errors without echoing arbitrary detail."""
    detail = exc.detail
    if isinstance(detail, dict) and detail.get("contract_version") == CONTROL_PLANE_CONTRACT_VERSION:
        body = dict(detail)
        body["http_status"] = exc.status_code
        request_id = _whoosh_request_id(request)
        upstream_request_id = _upstream_request_id(request)
        if request_id is not None:
            body["request_id"] = request_id
        if upstream_request_id is not None:
            body["upstream_request_id"] = upstream_request_id
        return JSONResponse(status_code=exc.status_code, content=body)
    status = int(exc.status_code)
    code = _error_code_for_status(status)
    return JSONResponse(
        status_code=status,
        content=_error_body_for_request(
            request,
            code,
            "Request failed" if status < 500 else "Internal error",
            http_status=status,
            details={"http_status": status},
        ),
    )


# ── Model resolution error handler ─────────────────────────────────────────

@app.exception_handler(ModelResolutionError)
async def _model_resolution_error_handler(request, exc: ModelResolutionError):
    return JSONResponse(
        status_code=404,
        content=_error_body_for_request(
            request,
            ErrorCode.MODEL_NOT_FOUND,
            "Requested model is not available",
            http_status=404,
            details={"model_alias": safe_model_alias(exc.model_id)},
        ),
    )


@app.exception_handler(BackendRequestPolicyError)
async def _backend_request_policy_error_handler(request, exc: BackendRequestPolicyError):
    """Reject inference-shaped unknown fields without echoing their values."""
    return JSONResponse(
        status_code=400,
        content=_error_body_for_request(
            request,
            ErrorCode.UNSUPPORTED_FIELD,
            "Unsupported request field",
            http_status=400,
            details={
                "failure_class": "validation",
                "policy_version": "cwc-006-v1",
                "rejected_field_count": len(exc.rejected_fields),
            },
        ),
    )


# ── Upstream runtime error handler ─────────────────────────────────────────

@app.exception_handler(UpstreamRuntimeError)
async def _upstream_runtime_error_handler(request, exc: UpstreamRuntimeError):
    """Classify upstream runtime errors into appropriate HTTP responses.

    Upstream errors carry their own http_status so the HTTP layer
    does not need conditionals per error type.
    """
    error_code = getattr(exc, "error_code", None) or _error_code_for_status(exc.http_status)

    return JSONResponse(
        status_code=exc.http_status,
        content=_error_body_for_request(
            request,
            error_code,
            safe_exception_message(exc),
            http_status=exc.http_status,
            details=_safe_upstream_error_detail(exc),
        ),
    )


# ── Generate ───────────────────────────────────────────────────────────────


@app.post("/v1/generate", response_model=GenerateResponse)
async def generate(request: Request, req: GenerateRequest) -> GenerateResponse:
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
            detail=_error_body_for_request(
                request,
                ErrorCode.RUNNER_OVERLOADED,
                "Whoosh'd is at its active request limit.",
                http_status=429,
                details={"active_jobs": rt.active_jobs},
            ),
        )

    rt.record_accepted()

    model = req.model_id or "stub-model"
    request_id = _begin_request_lifecycle(rt, request, model=model, stream=False)
    rt.mark_running(request_id)

    try:
        result = await router.generate(req, request_id=request_id)
        rt.complete_request(request_id)
        return result
    except UpstreamRuntimeError:
        rt.fail_request(request_id)
        raise
    except Exception as exc:
        rt.fail_request(request_id)
        raise HTTPException(
            status_code=500,
            detail=_error_body_for_request(
                request,
                ErrorCode.INTERNAL_ERROR,
                "Inference failed",
                http_status=500,
                details={"diagnostic": exception_metadata(exc)},
            ),
        ) from exc


# ── Streaming not-supported handler ────────────────────────────────────────

@app.exception_handler(StreamingNotSupportedError)
async def _streaming_not_supported_handler(request, exc: StreamingNotSupportedError):
    return JSONResponse(
        status_code=422,
        content=_error_body_for_request(
            request,
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Streaming is not supported by the selected runtime.",
            http_status=422,
            details={"failure_class": "unsupported_capability"},
        ),
    )


# ── Shared execution helpers (used by both queued and immediate paths) ────


async def _execute_non_streaming(
    adapter,
    req,
    ctx,
    rt,
    request_id,
    *,
    resolution: RuntimeResolution | None = None,
    queued: bool = False,
    batched: bool = False,
):
    """Execute a non-streaming request and return the JSON response."""
    upstream_request_id = normalize_identifier(
        getattr(ctx, "upstream_request_id", None)
    )
    req = ensure_backend_chat_request(
        req,
        adapter_kind=getattr(adapter, "kind", None),
        request_id=request_id,
    )
    try:
        result = await adapter.chat_completion(req, context=ctx)
        rt.complete_request(request_id)
        if resolution is not None:
            result = result.model_copy(
                update={
                    "runtime_provenance": resolution.provenance(
                        request_id=request_id,
                        upstream_request_id=upstream_request_id,
                        backend_reported_model_id=result.model,
                        streaming=False,
                        queued=queued,
                        batched=batched,
                        model_lifecycle=ModelLifecycleState.READY,
                    )
                }
            )
        return result
    except UpstreamRuntimeError:
        rt.fail_request(request_id)
        raise
    except Exception:
        rt.fail_request(request_id)
        raise


async def _execute_streaming(
    adapter,
    req,
    ctx,
    rt,
    request_id,
    *,
    resolution: RuntimeResolution | None = None,
    queued: bool = False,
):
    """Eagerly fetch the first SSE chunk, then return a StreamingResponse.

    No SSE chunks are emitted until the first chunk is successfully retrieved,
    so upstream errors surface as structured JSON before the stream starts.
    """
    upstream_request_id = normalize_identifier(
        getattr(ctx, "upstream_request_id", None)
    )
    req = ensure_backend_chat_request(
        req,
        adapter_kind=getattr(adapter, "kind", None),
        request_id=request_id,
    )
    stream_gen = adapter.chat_completion_stream(req, context=ctx)
    try:
        first_chunk = await stream_gen.__anext__()
    except StopAsyncIteration:
        async def _empty_sse():
            yield "data: [DONE]\n\n"
        rt.complete_request(request_id)
        empty_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        empty_headers.update(
            correlation_response_headers(
                upstream_request_id=upstream_request_id,
                whoosh_request_id=request_id,
            )
        )
        if resolution is not None:
            empty_headers[WHOOSHD_RUNTIME_PROVENANCE_HEADER] = (
                resolution.provenance(
                    request_id=request_id,
                    upstream_request_id=upstream_request_id,
                    streaming=True,
                    queued=queued,
                    model_lifecycle=ModelLifecycleState.READY,
                ).model_dump_json(exclude_none=True)
            )
        return StreamingResponse(
            _empty_sse(),
            media_type="text/event-stream",
            headers=empty_headers,
        )
    except UpstreamRuntimeError as exc:
        rt.fail_request(request_id)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(
                getattr(exc, "error_code", None) or _error_code_for_status(exc.http_status),
                safe_exception_message(exc),
                http_status=exc.http_status,
                request_id=request_id,
                upstream_request_id=upstream_request_id,
                details=_safe_upstream_error_detail(exc),
            ),
            headers=correlation_response_headers(
                upstream_request_id=upstream_request_id,
                whoosh_request_id=request_id,
            ),
        )
    except Exception:
        rt.fail_request(request_id)
        raise

    stream_provenance = None
    if resolution is not None:
        stream_provenance = resolution.provenance(
            request_id=request_id,
            upstream_request_id=upstream_request_id,
            backend_reported_model_id=first_chunk.model,
            streaming=True,
            queued=queued,
            model_lifecycle=ModelLifecycleState.READY,
        )
        # Keep the established OpenAI chunk shape stable.  Provenance is
        # available before visible output through a bounded response header.
        first_chunk = first_chunk.model_copy(
            update={"runtime_provenance": None}
        )

    async def _sse_stream():
        finished_normally = False
        try:
            yield first_chunk.to_sse()
            async for chunk in stream_gen:
                yield chunk.to_sse()
            yield "data: [DONE]\n\n"
            finished_normally = True
        except UpstreamRuntimeError as exc:
            error_json = json.dumps({
                "error": _error_body(
                    getattr(exc, "error_code", None)
                    or _error_code_for_status(exc.http_status),
                    safe_exception_message(exc),
                    http_status=exc.http_status,
                    request_id=request_id,
                    upstream_request_id=upstream_request_id,
                    details={
                        **_safe_upstream_error_detail(exc),
                        "output_started": True,
                    },
                )
            })
            yield f"data: {error_json}\n\n"
        finally:
            if finished_normally:
                rt.complete_request(request_id)
            else:
                rt.record_stream_disconnect(request_id)
                rt.cancel_request(request_id)

    stream_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    stream_headers.update(
        correlation_response_headers(
            upstream_request_id=upstream_request_id,
            whoosh_request_id=request_id,
        )
    )
    if stream_provenance is not None:
        stream_headers[WHOOSHD_RUNTIME_PROVENANCE_HEADER] = (
            stream_provenance.model_dump_json(exclude_none=True)
        )

    return StreamingResponse(
        _sse_stream(),
        media_type="text/event-stream",
        headers=stream_headers,
    )


# ── ThreadWake ephemeral execution bridge ────────────────────────────────


async def _execute_non_streaming_with_threadwake(
    adapter, req, ctx, rt, request_id,
    *,
    backend: str | None,
    threadwake_request=None,
    resolution: RuntimeResolution | None = None,
    queued: bool = False,
):
    """Execute non-streaming chat with optional ThreadWake ephemeral KV reuse.

    When ThreadWake is in ephemeral/session mode and the backend has
    registered KV + tokenizer capability, attempt ephemeral execution.
    On cache hit, returns the cached response.  On miss or fallback,
    delegates to the normal adapter execution path.

    Streaming requests are never routed through this bridge.
    """
    result = await _threadwake_manager.execute_ephemeral_chat_completion(
        threadwake_request or req,
        backend=backend or "unknown",
        full_generation_fn=lambda: adapter.chat_completion(req, context=ctx),
    )
    if result is not None:
        rt.complete_request(request_id)
        if resolution is not None:
            result = result.model_copy(
                update={
                    "runtime_provenance": resolution.provenance(
                        request_id=request_id,
                        upstream_request_id=normalize_identifier(
                            getattr(ctx, "upstream_request_id", None)
                        ),
                        backend_reported_model_id=result.model,
                        streaming=False,
                        queued=queued,
                        model_lifecycle=ModelLifecycleState.READY,
                    )
                }
            )
        return result

    # Fall through to normal adapter execution.
    return await _execute_non_streaming(
        adapter,
        req,
        ctx,
        rt,
        request_id,
        resolution=resolution,
        queued=queued,
    )


# ── OpenAI-compatible Chat Completions ─────────────────────────────────────


# ── OpenAI-compatible Chat Completions ─────────────────────────────────────


async def _try_execute_live_batch(
    *,
    selected_request_id: str,
    adapter,
    queue: Any,
    rt: Any,
    resolution: RuntimeResolution | None = None,
) -> Any | None:
    """Attempt live-path batch execution for the selected request.

    Returns a ChatCompletionResponse on success, or None if batching
    is not possible.  On batch failure after the backend call, resolves
    all claimed futures with an error response — no fallback to avoid
    duplicate execution.
    """
    from whooshd.batching import BatchAnalyzer
    from whooshd.config import (
        get_batch_analysis_enabled,
        get_batch_execution_enabled,
        get_batch_execution_min_size,
        get_batch_execution_max_size,
    )
    from whooshd.contracts import ErrorCode

    if not get_batch_execution_enabled():
        return None

    backend = getattr(adapter, "kind", None)
    cap = getattr(adapter, "supports_chat_batching", lambda: "unsupported")()
    if cap not in ("experimental",):
        return None

    analyzer = BatchAnalyzer(
        max_group_size=get_batch_execution_max_size(),
    )
    group = queue.find_batch_group(
        selected_request_id,
        analyzer=analyzer,
        backend=backend,
        enabled=get_batch_analysis_enabled(),
    )
    if group is None:
        return None

    # Exclude cancelled entries before claiming.
    group = [e for e in group if not getattr(e, "batch_claimed", False)]
    active_group = []
    for e in group:
        token = rt.get_cancellation_token(e.request_id)
        if token is not None and token.is_cancelled():
            logger.debug(
                "batch: excluding cancelled entry request_id=%s",
                e.request_id,
            )
            continue
        active_group.append(e)

    if len(active_group) < get_batch_execution_min_size():
        return None

    # Claim entries atomically.
    futures = queue.claim_batch_entries(active_group)
    if not futures:
        return None

    def _batch_error_response(entry, message: str) -> JSONResponse:
        """Build a canonical non-success response for a failed batch."""
        return JSONResponse(
            status_code=502,
            content=_error_body(
                ErrorCode.UPSTREAM_PROTOCOL_ERROR,
                "The inference batch failed before producing a valid response.",
                http_status=502,
                request_id=entry.request_id,
                upstream_request_id=entry.upstream_request_id,
                details={"batch_failure": message},
            ),
        )

    def _resolve_all_with_error(message: str) -> Any | None:
        """Resolve all claimed futures with an error response."""
        error_results = [
            (e.request_id, _batch_error_response(e, message))
            for e in active_group
        ]
        for e in active_group:
            rt.mark_running(e.request_id)
            rt.fail_request(e.request_id, error_code="BATCH_ERROR")
        queue.resolve_batch_results(active_group, error_results)

        for e in active_group:
            if e.request_id == selected_request_id:
                return next(
                    (response for request_id, response in error_results if request_id == e.request_id),
                    None,
                )
        return None

    try:
        requests = [
            ensure_backend_chat_request(
                e.request,
                adapter_kind=backend,
                request_id=e.request_id,
            )
            for e in active_group
        ]
        contexts = [
            RequestExecutionContext(
                request_id=entry.request_id,
                cancellation_token=token,
                stream=False,
                upstream_request_id=entry.upstream_request_id,
            )
            if (token := rt.get_cancellation_token(entry.request_id)) is not None
            else None
            for entry in active_group
        ]
        batch_responses = await adapter.chat_completion_batch(requests, contexts=contexts)

        if len(batch_responses) != len(active_group):
            logger.warning(
                "batch: response count mismatch expected=%d got=%d",
                len(active_group), len(batch_responses),
            )
            return _resolve_all_with_error("response count mismatch")

        # Resolve futures for peer entries.
        results = []
        response_by_request_id = {}
        for entry, response in zip(active_group, batch_responses):
            rt.mark_running(entry.request_id)
            rt.complete_request(entry.request_id)
            if resolution is not None:
                response = response.model_copy(
                    update={
                        "runtime_provenance": resolution.for_model(
                            entry.request.model
                        ).provenance(
                            request_id=entry.request_id,
                            upstream_request_id=entry.upstream_request_id,
                            backend_reported_model_id=response.model,
                            streaming=False,
                            queued=True,
                            batched=True,
                            model_lifecycle=ModelLifecycleState.READY,
                        )
                    }
                )
            results.append((entry.request_id, response))
            response_by_request_id[entry.request_id] = response

        queue.resolve_batch_results(active_group, results)

        # Return the selected entry's response.
        response = response_by_request_id.get(selected_request_id)
        if response is not None:
            queue.notify_capacity()
            return response

        return None
    except Exception:
        logger.warning("Batch execution failed, resolving all entries with error")
        return _resolve_all_with_error("backend error")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, req: ChatCompletionRequest):
    """OpenAI-compatible chat completion endpoint.

    - stream=false → JSON response with the full completion.
    - stream=true  → SSE text/event-stream with OpenAI-compatible chunks.

    Requests are routed to the correct runtime based on model ID.

    When the optional queue is enabled via WHOOSHD_ENABLE_QUEUE, requests
    that pass structural admission but exceed the active request limit
    are enqueued in a bounded FIFO queue instead of being rejected
    immediately.  The HTTP connection waits synchronously until execution
    starts or a timeout/cancellation occurs.
    """
    rt = get_runtime()
    router = get_router()
    queue = get_queue()

    # ── Admission control ─────────────────────────────────────────────
    admission = evaluate_chat_request(req, rt)

    # Queue-full is a hard rejection — record queue-specific counter too.
    if admission.reason == AdmissionDecision.REJECTED_QUEUE_FULL:
        rt.record_queue_rejected()
        rt.record_rejected(admission.reason.value)
        return JSONResponse(
            status_code=admission.http_status,
            content=_error_body_for_request(
                request,
                admission.error_code or ErrorCode.QUEUE_FULL,
                admission.message or "Request rejected.",
                http_status=admission.http_status,
                details=admission.details,
            ),
        )

    # All non-accepted, non-queued decisions are hard rejections.
    # QUEUED is accepted-for-waiting — let it fall through to the queue branch.
    if not admission.accepted and admission.reason != AdmissionDecision.QUEUED:
        rt.record_rejected(admission.reason.value)
        return JSONResponse(
            status_code=admission.http_status,
            content=_error_body_for_request(
                request,
                admission.error_code or ErrorCode.INVALID_REQUEST,
                admission.message or "Request rejected.",
                http_status=admission.http_status,
                details=admission.details,
            ),
        )

    # Accepted or queued — both count as non-rejected.
    rt.record_accepted()

    # ── Resolve the adapter via router ───────────────────────────────
    try:
        resolution = await router.resolve_model_runtime(req.model)
        adapter = resolution.adapter
    except ModelResolutionError as exc:
        rt.record_rejected("rejected_model_not_ready")
        return JSONResponse(
            status_code=404,
            content=_error_body_for_request(
                request,
                ErrorCode.MODEL_NOT_FOUND,
                "Requested model is not available",
                http_status=404,
                details={"model_alias": safe_model_alias(exc.model_id)},
            ),
        )

    # ── Vision capability check ──────────────────────────────────────
    has_image = _request_has_image_content(req)
    if has_image:
        # Check if the resolved adapter supports vision.
        adapter_supports_vision = _adapter_supports_vision(adapter)
        if not adapter_supports_vision:
            rt.record_rejected("rejected_model_not_ready")
            return JSONResponse(
                status_code=422,
                content=_error_body_for_request(
                    request,
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "The selected runtime does not support image input.",
                    http_status=422,
                    details={"model_alias": safe_model_alias(req.model), "body_present": True},
                ),
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

    # ThreadWake and lifecycle code retain the ingress request.  Every
    # execution mode receives a separate backend representation so controls
    # and undeclared extras cannot be restored by queueing, batching, or
    # fallback code.
    try:
        backend_request = ensure_backend_chat_request(
            req,
            adapter_kind=getattr(adapter, "kind", None),
        )
    except BackendRequestPolicyError as exc:
        rt.record_rejected("rejected_unsupported_fields")
        return JSONResponse(
            status_code=400,
            content=_error_body_for_request(
                request,
                ErrorCode.UNSUPPORTED_FIELD,
                "Unsupported request field",
                http_status=400,
                details={
                    "failure_class": "validation",
                    "policy_version": "cwc-006-v1",
                    "rejected_field_count": len(exc.rejected_fields),
                },
            ),
        )

    # ── Queue wait (when admission returned QUEUED) ───────────────────
    is_queued = admission.reason == AdmissionDecision.QUEUED
    if is_queued:
        # Create the request record and transition to queued.
        request_id = _begin_request_lifecycle(
            rt, request, model=req.model, stream=req.stream
        )
        rt.mark_queued(request_id)
        token = rt.get_cancellation_token(request_id)
        entry = QueueEntry(
            request_id=request_id,
            request=backend_request,
            upstream_request_id=_upstream_request_id(request),
        )
        queue.enqueue(entry)

        from whooshd.config import get_max_active_requests

        ready = await queue.wait_for_execution(
            entry,
            cancel_token=token,
            capacity_available=lambda: rt.active_jobs < get_max_active_requests(),
        )

        if not ready:
            # Timed out or cancelled — never call the adapter.
            if token is not None and token.is_cancelled():
                rt.cancel_request(request_id)
                rt.record_queue_cancelled()
                # Return cancellation-compatible response.
                return JSONResponse(
                    status_code=409,
                    content=_error_body_for_request(
                        request,
                        ErrorCode.CANCELLED,
                        "Request was cancelled while queued.",
                        http_status=409,
                        request_id=request_id,
                        details={"request_id": request_id},
                    ),
                )
            else:
                rt.mark_timed_out(request_id)
                return JSONResponse(
                    status_code=504,
                    content=_error_body_for_request(
                        request,
                        ErrorCode.TIMEOUT,
                        "Request timed out while waiting in queue.",
                        http_status=504,
                        request_id=request_id,
                        details={"request_id": request_id},
                    ),
                )

        # Successfully dequeued — proceed to execution.
        rt.mark_dequeued(request_id)

        # ── Live-path batch attempt (non-streaming, stub, gated) ─────
        if not req.stream:
            batch_response = await _try_execute_live_batch(
                selected_request_id=request_id,
                adapter=adapter,
                queue=queue,
                rt=rt,
                resolution=resolution,
            )
            if batch_response is not None:
                return batch_response

        # ── Non-streaming: dequeue then execute ───────────────────────
        if not req.stream:
            rt.mark_running(request_id)
            ctx = _execution_context(
                request,
                request_id=request_id,
                cancellation_token=token,
                stream=False,
            )
            return await _execute_non_streaming_with_threadwake(
                adapter, backend_request, ctx, rt, request_id,
                backend=getattr(adapter, "kind", None),
                threadwake_request=req,
                resolution=resolution,
                queued=True,
            )

        # ── Streaming: dequeue then stream ────────────────────────────
        # Note: no SSE chunks are emitted while queued — the connection
        # is held open during the queue wait above.
        if not adapter.supports_streaming:
            rt.fail_request(request_id)
            raise StreamingNotSupportedError()

        rt.mark_streaming(request_id)
        ctx = _execution_context(
            request,
            request_id=request_id,
            cancellation_token=token,
            stream=True,
        )
        return await _execute_streaming(
            adapter,
            backend_request,
            ctx,
            rt,
            request_id,
            resolution=resolution,
            queued=True,
        )

    # ── Non-streaming path (immediate execution) ──────────────────────
    if not req.stream:
        request_id = _begin_request_lifecycle(rt, request, model=req.model, stream=False)
        rt.mark_running(request_id)
        token = rt.get_cancellation_token(request_id)
        ctx = _execution_context(
            request,
            request_id=request_id,
            cancellation_token=token,
            stream=False,
        )
        return await _execute_non_streaming_with_threadwake(
            adapter, backend_request, ctx, rt, request_id,
            backend=getattr(adapter, "kind", None),
            threadwake_request=req,
            resolution=resolution,
            queued=False,
        )

    # ── Streaming path (immediate execution) ──────────────────────────
    if not adapter.supports_streaming:
        raise StreamingNotSupportedError()

    request_id = _begin_request_lifecycle(rt, request, model=req.model, stream=True)
    rt.mark_streaming(request_id)
    token = rt.get_cancellation_token(request_id)
    ctx = _execution_context(
        request,
        request_id=request_id,
        cancellation_token=token,
        stream=True,
    )
    return await _execute_streaming(
        adapter,
        backend_request,
        ctx,
        rt,
        request_id,
        resolution=resolution,
        queued=False,
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
async def runtime_cancel_request(request_id: str, request: Request):
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
            detail=_error_body_for_request(
                request,
                ErrorCode.INTERNAL_ERROR,
                "Request was not found.",
                http_status=404,
            ),
        )
    # The cancellation target owns the correlation pair.  An optional header
    # on the cancellation operation must not overwrite it.
    request.state.whoosh_request_id = snap.request_id
    request.state.upstream_request_id = snap.upstream_request_id
    if snap.status in (
        RequestLifecycleState.COMPLETED,
        RequestLifecycleState.CANCELLED,
        RequestLifecycleState.FAILED,
        RequestLifecycleState.TIMED_OUT,
    ):
        raise HTTPException(
            status_code=409,
            detail=_error_body_for_request(
                request,
                ErrorCode.CANCELLED,
                "Request is already in a terminal state.",
                http_status=409,
                details={"status": snap.status.value},
            ),
        )
    signalled = rt.request_cancellation(request_id)
    if signalled:
        rt.cancel_request(request_id)
    return {
        "ok": True,
        "request_id": snap.request_id,
        "upstream_request_id": snap.upstream_request_id,
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
    # This is a broker-level snapshot; per-lane detail lives at /health/runtime.
    # Surface a non-stub lane by name only when it is actually loaded, so an
    # always-registered-but-offline lane (e.g. llama.cpp) is never reported as
    # "the" adapter.  Otherwise keep the multi-runtime identity.
    adapter_name = "multi-runtime"
    for kind in router.registered_kinds:
        if kind == "stub":
            continue
        adapter = router.get_adapter(kind)
        if adapter and adapter.is_loaded():
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
        real_adapter_ready = any(
            kind != RuntimeKind.STUB.value and status == "ready"
            for kind, status in results.items()
        )
        has_real_adapters = any(
            kind != RuntimeKind.STUB.value for kind in results
        )
        if has_real_adapters and not real_adapter_ready:
            rt.fail_warmup(
                error_code="MODEL_LOAD_FAILED",
                error_message="All non-stub adapters failed warmup.",
            )
        else:
            rt.complete_warmup()
    except Exception as exc:
        rt.fail_warmup(
            error_code="MODEL_LOAD_FAILED",
            error_message=exception_metadata(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=_error_body(
                ErrorCode.MODEL_LOAD_FAILED,
                "Model warmup failed",
                http_status=500,
                details={"diagnostic": exception_metadata(exc)},
            ),
        ) from exc

    configured = get_advertised_model_id()
    return rt.build_model_snapshot(
        adapter_name="multi-runtime",
        configured_model=configured,
        adapter_results=results,
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
            detail=_error_body(
                ErrorCode.CANCELLED,
                "Cannot unload while requests are active.",
                http_status=409,
                details={"active_jobs": rt.active_jobs},
            ),
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


# ── ThreadWake analysis visibility ──────────────────────────────────────────


@app.get("/runtime/threadwake/analysis")
async def runtime_threadwake_analysis():
    """Read-only ThreadWake analysis report.

    Returns candidate, manifest, and artifact summary counts.
    May run the metadata-only analysis loop on demand.
    No raw prompts, token IDs, opaque refs, or KV state.
    """
    loop = _get_analysis_loop()
    result = loop.run(limit=50)
    last = loop.last_result() or {}
    return {
        "analysis": {
            "candidates_scanned": last.get("candidates_scanned", 0),
            "candidates_eligible": last.get("candidates_eligible", 0),
            "manifests_created": last.get("manifests_created", 0),
            "artifacts_registered": last.get("artifacts_registered", 0),
            "skipped": last.get("skipped", 0),
            "errors": last.get("errors", 0),
            "run_count": loop.run_count,
        },
        "threadwake_status": {
            "enabled": _threadwake_manager.get_health().get("enabled", False),
            "mode": _threadwake_manager.get_health().get("mode", "off"),
        },
    }

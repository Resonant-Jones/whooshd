"""Whoosh'd FastAPI application.

v0 endpoints: health, runtime, models, generate (stub),
chat completions (OpenAI-compatible stub), model inventory aliases.
MLX inference lives behind the adapter boundary.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from whooshd import __version__
from whooshd.admission import evaluate_chat_request
from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.adapters.factory import create_adapter
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
    RequestLifecycleState,
    RunnerStatus,
)
from whooshd.config import get_mlx_model_path
from whooshd.runtime import get_runtime

app = FastAPI(
    title="Whoosh'd",
    description="Memory-aware local inference broker for Apple Silicon",
    version=__version__,
)


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


# ── Readiness ──────────────────────────────────────────────────────────────


@app.get("/ready")
async def ready():
    """Readiness probe — can this provider accept inference right now?

    Returns 200 when ready, 503 when the process is alive but the model
    is not ready to serve inference (warming, unloaded, failed, degraded).
    """
    rt = get_runtime()
    adapter = get_inference_adapter()
    configured = get_mlx_model_path()

    lifecycle = rt.model_lifecycle
    is_ready = lifecycle == ModelLifecycleState.READY

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
        adapter=adapter.name,
        configured_model=configured,
        loaded_model=adapter.model_id() if adapter.is_loaded() else None,
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
    """List registered models and their load state."""
    rt = get_runtime()
    return ModelsResponse(models=rt.list_models())


# ── Inference adapter (selected by WHOOSHD_ADAPTER env var) ──────────────

_inference_adapter = create_adapter()


def get_inference_adapter():
    """Return the current inference adapter.

    Controlled by the WHOOSHD_ADAPTER environment variable.
    Default: stub.  Set to 'mlx' for real mlx-lm inference.
    """
    return _inference_adapter


# ── Startup: sync lifecycle with adapter reality ────────────────────────────


def _init_lifecycle():
    """If the adapter is already loaded, mark the runtime lifecycle as ready."""
    rt = get_runtime()
    adapter = get_inference_adapter()
    if adapter.is_loaded():
        rt.complete_warmup()


_init_lifecycle()


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


# ── Generate ───────────────────────────────────────────────────────────────


@app.post("/v1/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Generate text from a prompt using the configured inference adapter.

    Currently returns deterministic stub output. Phase 1B wires mlx-lm.
    """
    rt = get_runtime()

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

    adapter = get_inference_adapter()
    try:
        result = await adapter.generate(req)
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
    """
    rt = get_runtime()
    adapter = get_inference_adapter()

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

    # ── Non-streaming path ────────────────────────────────────────────
    if not req.stream:
        request_id = rt.begin_request(model=req.model, stream=False)
        rt.mark_running(request_id)
        token = rt.get_cancellation_token(request_id)
        try:
            result = await adapter.chat_completion(req)
            rt.complete_request(request_id)
            return result
        except Exception:
            rt.fail_request(request_id)
            raise

    # ── Streaming path ────────────────────────────────────────────────
    if not adapter.supports_streaming:
        raise StreamingNotSupportedError()

    request_id = rt.begin_request(model=req.model, stream=True)
    rt.mark_streaming(request_id)
    token = rt.get_cancellation_token(request_id)

    async def _sse_stream():
        finished_normally = False
        try:
            async for chunk in adapter.chat_completion_stream(req):
                if token and token.is_cancelled():
                    break
                yield chunk.to_sse()
            yield "data: [DONE]\n\n"
            finished_normally = True
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
    """OpenAI-compatible model list."""
    rt = get_runtime()
    return rt.build_openai_model_list()


# ── Ollama-compatible Tags (model inventory alias) ─────────────────────────


@app.get("/api/tags")
async def ollama_tags():
    """Ollama-compatible model tags list."""
    rt = get_runtime()
    return rt.build_ollama_tags()


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
    adapter = get_inference_adapter()
    configured = get_mlx_model_path()
    return rt.build_model_snapshot(
        adapter_name=adapter.name, configured_model=configured
    )


@app.post("/runtime/model/warmup")
async def runtime_model_warmup():
    """Trigger model warmup / loading.

    For MLX: calls adapter.warmup() which loads the model.
    For stub: instant no-op.
    Status transitions through WARMING → READY on success,
    or FAILED on load error.
    """
    rt = get_runtime()
    adapter = get_inference_adapter()

    rt.begin_warmup()
    try:
        await adapter.warmup()
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

    configured = get_mlx_model_path()
    return rt.build_model_snapshot(
        adapter_name=adapter.name, configured_model=configured
    )


@app.post("/runtime/model/unload")
async def runtime_model_unload():
    """Unload the model from memory.

    Returns 409 Conflict if active requests are running.
    For MLX: releases model/tokenizer references and hints the GC.
    For stub: no-op.
    """
    rt = get_runtime()
    adapter = get_inference_adapter()

    if rt.active_jobs > 0:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCode.INTERNAL,
                message=f"Cannot unload: {rt.active_jobs} active request(s) in progress.",
            ).model_dump(),
        )

    await adapter.unload()
    rt.complete_unload()

    configured = get_mlx_model_path()
    return rt.build_model_snapshot(
        adapter_name=adapter.name, configured_model=configured
    )


# ── Internal runtime: admission config ─────────────────────────────────────


@app.get("/runtime/admission")
async def runtime_admission():
    """Current admission limits and counters."""
    rt = get_runtime()
    return rt.build_admission_config()

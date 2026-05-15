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
from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.adapters.stub import StubInferenceAdapter
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorCode,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    ModelsResponse,
)
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
        active_model=rt.active_model,
        queue_depth=rt.queue_depth,
        active_jobs=rt.active_jobs,
        memory=rt.memory,
    )


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


# ── Inference adapter (stub by default) ───────────────────────────────────

_inference_adapter = StubInferenceAdapter()


def get_inference_adapter():
    """Return the current inference adapter.

    Replaced in Phase 1B when MLX is wired in.
    """
    return _inference_adapter


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
    adapter = get_inference_adapter()
    try:
        return await adapter.generate(req)
    except Exception as exc:
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
    adapter = get_inference_adapter()

    if not req.stream:
        return await adapter.chat_completion(req)

    # ── Streaming path ────────────────────────────────────────────────
    if not adapter.supports_streaming:
        raise StreamingNotSupportedError()

    async def _sse_stream():
        async for chunk in adapter.chat_completion_stream(req):
            yield chunk.to_sse()
        yield "data: [DONE]\n\n"

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

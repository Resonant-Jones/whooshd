"""Whoosh'd FastAPI application.

v0 endpoints: health, runtime, models.  No MLX inference yet.
"""

from __future__ import annotations

from fastapi import FastAPI

from whooshd import __version__
from whooshd.contracts import HealthResponse, ModelsResponse
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

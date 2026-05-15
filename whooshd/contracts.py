"""Typed request/response contracts for the Whoosh'd API.

These models define the API surface. They must stay stable once published
so Codexify's MLXProviderAdapter can depend on them without drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Health ──────────────────────────────────────────────────────────────────


class MemoryPressure(str, Enum):
    """Unified memory pressure level reported by the runner."""

    LOW = "low"
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class MemoryInfo(BaseModel):
    """Snapshot of unified memory state."""

    pressure: MemoryPressure = MemoryPressure.NORMAL
    total_gb: float = Field(..., ge=0, description="Total unified memory in GiB")
    used_gb: float = Field(..., ge=0, description="Currently used unified memory in GiB")
    available_gb: float = Field(..., ge=0, description="Estimated available unified memory in GiB")


class HealthResponse(BaseModel):
    """GET /health response."""

    ok: bool = True
    runner: str = "whooshd"
    version: str = Field(..., description="Runner version")
    active_model: Optional[str] = Field(None, description="Currently loaded model ID, or null")
    queue_depth: int = Field(0, ge=0, description="Number of jobs waiting in queue")
    active_jobs: int = Field(0, ge=0, description="Number of currently executing jobs")
    memory: MemoryInfo = Field(default_factory=MemoryInfo)


# ── Runtime ─────────────────────────────────────────────────────────────────


class LoadedModelInfo(BaseModel):
    """Summary of a single loaded model."""

    id: str = Field(..., description="Model ID from registry")
    weight_footprint_gb: float = Field(0.0, ge=0, description="Weight memory in GiB")
    kv_cache_gb: float = Field(0.0, ge=0, description="KV cache memory in GiB")
    active_requests: int = Field(0, ge=0, description="Requests actively using this model")


class ConcurrencyBudget(BaseModel):
    """Computed concurrency envelope for the current resource state."""

    max_active_jobs: int = Field(1, ge=0, description="Safe ceiling on concurrent jobs")
    estimated_safe_concurrency: int = Field(1, ge=0, description="Recommended concurrency given current memory")
    queue_capacity: int = Field(32, ge=0, description="Max queued jobs before rejection")


class RuntimeResponse(BaseModel):
    """GET /runtime response — full snapshot of runner state."""

    memory: MemoryInfo = Field(default_factory=MemoryInfo)
    loaded_models: list[LoadedModelInfo] = Field(default_factory=list)
    concurrency: ConcurrencyBudget = Field(default_factory=ConcurrencyBudget)
    uptime_seconds: float = Field(0.0, ge=0, description="Seconds since runner start")


# ── Models ──────────────────────────────────────────────────────────────────


class ModelCapability(str, Enum):
    CHAT = "chat"
    STREAMING = "streaming"
    JSON = "json"
    TOOLS = "tools"
    EMBEDDINGS = "embeddings"


class ModelInfo(BaseModel):
    """Descriptor for a registered model (loaded or not)."""

    id: str = Field(..., description="Unique model identifier")
    loaded: bool = Field(False, description="Whether the model is currently in memory")
    capabilities: list[ModelCapability] = Field(default_factory=list)
    max_concurrent_jobs: int = Field(1, ge=1, description="Max concurrent jobs for this model")
    context_window: int = Field(32768, ge=0, description="Max context window in tokens")
    quantization: Optional[str] = Field(None, description="Quantization level, e.g. 4bit, 8bit")
    memory_class: str = Field("small", description="small | medium | large — sizing hint")


class ModelsResponse(BaseModel):
    """GET /models response."""

    models: list[ModelInfo] = Field(default_factory=list)


# ── Error ───────────────────────────────────────────────────────────────────


class ErrorCode(str, Enum):
    MEMORY_PRESSURE = "MEMORY_PRESSURE"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    TIMEOUT = "TIMEOUT"
    RUNNER_OVERLOADED = "RUNNER_OVERLOADED"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"


class ErrorResponse(BaseModel):
    """Standard error body returned for non-2xx responses."""

    code: ErrorCode
    message: str
    retry_after_seconds: Optional[float] = Field(None, description="Suggested backoff in seconds")
    detail: Optional[dict] = Field(None, description="Optional machine-readable detail")

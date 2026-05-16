"""Typed request/response contracts for the Whoosh'd API.

These models define the API surface. They must stay stable once published
so Codexify's MLXProviderAdapter can depend on them without drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Shared enums ────────────────────────────────────────────────────────────


class RunnerStatus(str, Enum):
    """Fine-grained runner lifecycle state.

    Distinguishes process-alive from model-ready so UIs and orchestrators
    never silently collapse warmup into offline.
    """

    STARTING = "starting"
    WARMING = "warming"
    READY = "ready"
    GENERATING = "generating"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class ModelLifecycleState(str, Enum):
    """Per-model lifecycle state — independent of process liveness.

    A process can be alive (RunnerStatus.READY) while the model is
    unloaded or failed.  Codexify uses this to distinguish
    "wait for warmup" from "route to fallback provider".
    """

    UNLOADED = "unloaded"
    WARMING = "warming"
    READY = "ready"
    GENERATING = "generating"
    DEGRADED = "degraded"
    FAILED = "failed"


class MemoryPressure(str, Enum):
    """Unified memory pressure level reported by the runner."""

    LOW = "low"
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


# ── Health ──────────────────────────────────────────────────────────────────


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
    status: RunnerStatus = RunnerStatus.READY
    model_lifecycle: ModelLifecycleState = ModelLifecycleState.UNLOADED
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
    model_lifecycle: ModelLifecycleState = ModelLifecycleState.UNLOADED


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


# ── Generation ─────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """POST /v1/generate request body."""

    prompt: str = Field(..., min_length=1, description="Input text prompt")
    model_id: Optional[str] = Field(None, description="Target model ID; uses active model if omitted")
    max_tokens: int = Field(256, ge=1, le=16384, description="Maximum tokens to generate")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: float = Field(0.95, ge=0.0, le=1.0, description="Nucleus sampling threshold")
    stop: Optional[list[str]] = Field(None, description="Stop sequences")
    request_id: Optional[str] = Field(None, description="Client-supplied idempotency key")


class TokenUsage(BaseModel):
    """Token accounting for a generation request."""

    prompt_tokens: Optional[int] = Field(None, ge=0, description="Tokens in the input prompt")
    completion_tokens: Optional[int] = Field(None, ge=0, description="Tokens in the generated response")
    total_tokens: Optional[int] = Field(None, ge=0, description="Sum of prompt and completion tokens")


class ResponseRuntimeInfo(BaseModel):
    """Runtime metadata about how the generation was served."""

    adapter: str = Field(..., description="Name of the inference adapter used")
    queued: bool = Field(False, description="Whether the request spent time in queue")
    elapsed_ms: float = Field(..., ge=0, description="Wall-clock time from request to response")


class GenerateResponse(BaseModel):
    """POST /v1/generate response body."""

    ok: bool = True
    request_id: str = Field(..., description="Idempotency key for this generation")
    model_id: Optional[str] = Field(None, description="Model that served the request")
    text: str = Field(..., description="Generated text")
    finish_reason: str = Field("stop", description="Reason generation stopped")
    usage: TokenUsage = Field(default_factory=TokenUsage)
    runtime: ResponseRuntimeInfo = Field(..., description="How this generation was served")


# ── OpenAI-compatible Chat Completions ─────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in a chat completion conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(..., min_length=1, description="Text content of the message")
    name: Optional[str] = Field(None, description="Optional speaker name")


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible POST /v1/chat/completions request body."""

    model: str = Field(..., min_length=1, description="Model ID to use for completion")
    messages: list[ChatMessage] = Field(..., min_length=1, description="Conversation messages")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: float = Field(0.95, ge=0.0, le=1.0, description="Nucleus sampling threshold")
    max_tokens: Optional[int] = Field(256, ge=1, le=32768, description="Maximum tokens to generate")
    stream: bool = Field(False, description="Whether to stream response tokens via SSE")
    stop: Optional[list[str]] = Field(None, description="Stop sequences")
    user: Optional[str] = Field(None, description="End-user identifier for abuse monitoring")


class ChatCompletionChoice(BaseModel):
    """A single completion choice."""

    index: int = Field(0, ge=0)
    message: ChatMessage
    finish_reason: str = Field("stop", description="Reason generation stopped")


class ChatCompletionUsage(BaseModel):
    """Token usage for a chat completion."""

    prompt_tokens: Optional[int] = Field(None, ge=0, description="Tokens in the input messages")
    completion_tokens: Optional[int] = Field(None, ge=0, description="Tokens in the generated response")
    total_tokens: Optional[int] = Field(None, ge=0, description="Sum of prompt and completion tokens")


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response body."""

    id: str = Field(..., description="Unique completion identifier")
    object: str = Field("chat.completion", description="Object type")
    created: int = Field(..., description="Unix timestamp of creation")
    model: str = Field(..., description="Model that served the request")
    choices: list[ChatCompletionChoice] = Field(..., min_length=1)
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage)


# ── OpenAI-compatible Streaming Chat Chunks ────────────────────────────────


class ChatCompletionDelta(BaseModel):
    """Delta content for a streaming chat completion chunk."""

    role: Optional[Literal["assistant"]] = Field(None, description="Set on the first chunk only")
    content: Optional[str] = Field(None, description="Token or word fragment")


class ChatCompletionChunkChoice(BaseModel):
    """A single choice delta in a streaming chunk."""

    index: int = Field(0, ge=0)
    delta: ChatCompletionDelta = Field(default_factory=ChatCompletionDelta)
    finish_reason: Optional[str] = Field(None, description="Set on the final chunk only")


class ChatCompletionChunk(BaseModel):
    """OpenAI-compatible streaming chat completion chunk.

    Serialised as an SSE data line: data: {json}\n\n
    """

    id: str = Field(..., description="Completion identifier, consistent across the stream")
    object: str = Field("chat.completion.chunk", description="Object type")
    created: int = Field(..., description="Unix timestamp of creation")
    model: str = Field(..., description="Model that served the request")
    choices: list[ChatCompletionChunkChoice] = Field(..., min_length=1)

    def to_sse(self) -> str:
        """Render this chunk as an SSE data line with trailing blank line."""
        return f"data: {self.model_dump_json()}\n\n"


# ── OpenAI-compatible Model List ───────────────────────────────────────────


class OpenAIModelEntry(BaseModel):
    """A single model entry in an OpenAI-style /v1/models response."""

    id: str = Field(..., description="Model identifier")
    object: str = Field("model", description="Object type")
    created: int = Field(..., description="Unix timestamp when model was registered")
    owned_by: str = Field("whooshd", description="Owning entity")


class OpenAIModelListResponse(BaseModel):
    """OpenAI-compatible GET /v1/models response."""

    object: str = Field("list", description="Object type")
    data: list[OpenAIModelEntry] = Field(default_factory=list)


# ── Request Lifecycle ──────────────────────────────────────────────────────


class RequestLifecycleState(str, Enum):
    """States a request moves through from acceptance to termination."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    STREAMING = "streaming"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RequestSnapshot(BaseModel):
    """Public-facing summary of a single request.

    Deliberately excludes prompt text and message content — this is
    runtime metadata only.
    """

    request_id: str = Field(..., description="Unique request identifier")
    model: str = Field(..., description="Model ID used for the request")
    stream: bool = Field(..., description="Whether this is a streaming request")
    status: RequestLifecycleState = Field(..., description="Current lifecycle state")
    started_at: float = Field(..., description="Unix timestamp when request was accepted")
    ended_at: Optional[float] = Field(None, description="Unix timestamp when request terminated")
    error_code: Optional[str] = Field(None, description="ErrorCode if status is failed")


class RequestListResponse(BaseModel):
    """Response for GET /runtime/requests."""

    requests: list[RequestSnapshot] = Field(default_factory=list)
    active_count: int = Field(0, ge=0, description="Count of requests in non-terminal states")


# ── Model Lifecycle ─────────────────────────────────────────────────────────


class ModelRuntimeSnapshot(BaseModel):
    """Public-safe snapshot of the loaded model's lifecycle.

    No prompts, messages, generated text, or raw tracebacks are included.
    """

    adapter: str = Field(..., description="Active adapter name (e.g. stub, mlx-lm)")
    configured_model: Optional[str] = Field(None, description="Model path from config")
    loaded_model: Optional[str] = Field(None, description="Model currently in memory, if any")
    lifecycle_state: ModelLifecycleState = Field(ModelLifecycleState.UNLOADED)
    loaded: bool = Field(False, description="Whether a model is currently loaded")
    warming: bool = Field(False, description="Whether a warmup is in progress")
    last_load_started_at: Optional[float] = Field(None, description="Unix timestamp")
    last_load_completed_at: Optional[float] = Field(None, description="Unix timestamp")
    last_unloaded_at: Optional[float] = Field(None, description="Unix timestamp")
    last_error_code: Optional[str] = Field(None, description="ErrorCode from the most recent failure")
    last_error_message: Optional[str] = Field(None, description="Short error message (no tracebacks)")


# ── Readiness ───────────────────────────────────────────────────────────────


class ReadinessResponse(BaseModel):
    """GET /ready response — can this provider accept inference right now?

    /ready is about readiness, not liveness.  A 503 here means the
    process is reachable but the model is not ready.  /health remains
    the liveness probe.
    """

    ready: bool = Field(False, description="True if the provider can accept inference requests")
    status: RunnerStatus = Field(..., description="Current runner status")
    model_lifecycle: ModelLifecycleState = Field(..., description="Current model lifecycle state")
    adapter: str = Field(..., description="Active adapter name")
    configured_model: Optional[str] = Field(None, description="Model path from config")
    loaded_model: Optional[str] = Field(None, description="Model currently in memory, if any")
    reason: Optional[str] = Field(None, description="Human-readable reason when not ready")


# ── Ollama-compatible Tags ─────────────────────────────────────────────────


class OllamaTagEntry(BaseModel):
    """A single model entry in an Ollama-style /api/tags response."""

    name: str = Field(..., description="Model name with optional tag suffix")
    modified_at: str = Field(..., description="ISO-8601 timestamp of last modification")
    size: int = Field(..., ge=0, description="Model size in bytes")


class OllamaTagsResponse(BaseModel):
    """Ollama-compatible GET /api/tags response."""

    models: list[OllamaTagEntry] = Field(default_factory=list)

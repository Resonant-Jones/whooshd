"""Runtime state manager with request lifecycle tracking.

Owns:
  * runner status, memory, model registry (stubbed)
  * request lifecycle bookkeeping (begin / complete / cancel / fail)
  * active_jobs computed from live request state
  * snapshot APIs for /runtime and /runtime/requests
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from whooshd.contracts import (
    CancellationToken,
    ConcurrencyBudget,
    LoadedModelInfo,
    MemoryInfo,
    MemoryPressure,
    ModelCapability,
    ModelInfo,
    ModelLifecycleState,
    ModelRuntimeSnapshot,
    OllamaTagEntry,
    OllamaTagsResponse,
    OpenAIModelEntry,
    OpenAIModelListResponse,
    RequestLifecycleState,
    RequestListResponse,
    RequestSnapshot,
    RunnerStatus,
    RuntimeResponse,
)

# Synthetic creation timestamp for stub models.
_STUB_MODEL_CREATED = 1700000000
# Synthetic size in bytes for stub models (~1.5 GB for a 1.5B 4-bit model).
_STUB_MODEL_SIZE = 1_500_000_000


@dataclass
class _RequestRecord:
    """Internal request bookkeeping — never exposed directly via API."""

    request_id: str
    model: str
    stream: bool
    status: RequestLifecycleState = RequestLifecycleState.ACCEPTED
    cancel_requested: bool = False
    cancel_token: CancellationToken | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    error_code: Optional[str] = None


class RuntimeState:
    """Singleton holder for the runner's live state.

    Every field exposed through the API lives here so the HTTP layer stays thin.
    """

    def __init__(self) -> None:
        self._started_at: float = time.monotonic()

        # ── Runner status ───────────────────────────────────────────────
        self.status: RunnerStatus = RunnerStatus.READY
        self.model_lifecycle: ModelLifecycleState = ModelLifecycleState.UNLOADED

        # ── Model lifecycle timestamps / errors ────────────────────────
        self._last_load_started_at: Optional[float] = None
        self._last_load_completed_at: Optional[float] = None
        self._last_unloaded_at: Optional[float] = None
        self._last_error_code: Optional[str] = None
        self._last_error_message: Optional[str] = None

        # ── Admission counters ────────────────────────────────────────
        self.total_requests_accepted: int = 0
        self.total_requests_rejected: int = 0
        self.total_rejected_overloaded: int = 0
        self.total_rejected_prompt_too_large: int = 0
        self.total_rejected_too_many_messages: int = 0
        self.total_rejected_max_tokens: int = 0
        self.total_rejected_model_not_ready: int = 0

        # ── Cancellation counters ─────────────────────────────────────
        self.total_requests_cancel_requested: int = 0
        self.total_requests_cancelled: int = 0
        self.total_stream_disconnects: int = 0

        # ── Memory (stubbed) ────────────────────────────────────────────
        self.memory = MemoryInfo(
            pressure=MemoryPressure.NORMAL,
            total_gb=32.0,
            used_gb=4.2,
            available_gb=27.8,
        )

        # ── Models (stubbed) ────────────────────────────────────────────
        self._models: dict[str, ModelInfo] = {
            "qwen2.5-1.5b-instruct-mlx": ModelInfo(
                id="qwen2.5-1.5b-instruct-mlx",
                loaded=False,
                capabilities=[
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.JSON,
                ],
                max_concurrent_jobs=2,
                context_window=32768,
                quantization="4bit",
                memory_class="small",
            ),
        }

        # ── Loaded-model snapshots (stubbed) ────────────────────────────
        self._loaded_snapshots: list[LoadedModelInfo] = []

        # ── Concurrency (stubbed) ───────────────────────────────────────
        self.concurrency = ConcurrencyBudget(
            max_active_jobs=1,
            estimated_safe_concurrency=1,
            queue_capacity=32,
        )

        # ── Request lifecycle ───────────────────────────────────────────
        self._requests: dict[str, _RequestRecord] = {}

        # ── Queue (stubbed — real scheduler later) ──────────────────────
        self.queue_depth: int = 0
        self.active_model: Optional[str] = None

    # ── Computed properties ─────────────────────────────────────────────

    @property
    def active_jobs(self) -> int:
        """Count of requests in non-terminal lifecycle states."""
        return sum(
            1
            for r in self._requests.values()
            if r.status
            in (
                RequestLifecycleState.ACCEPTED,
                RequestLifecycleState.RUNNING,
                RequestLifecycleState.STREAMING,
            )
        )

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started_at

    # ── API builders ────────────────────────────────────────────────────

    def build_runtime_response(self) -> RuntimeResponse:
        return RuntimeResponse(
            memory=self.memory,
            loaded_models=list(self._loaded_snapshots),
            concurrency=self.concurrency,
            uptime_seconds=self.uptime_seconds,
            model_lifecycle=self.model_lifecycle,
        )

    def build_model_snapshot(
        self, *, adapter_name: str, configured_model: Optional[str]
    ) -> ModelRuntimeSnapshot:
        """Build a public-safe model lifecycle snapshot."""
        from whooshd.app import get_inference_adapter

        adapter = get_inference_adapter()
        return ModelRuntimeSnapshot(
            adapter=adapter_name,
            configured_model=configured_model,
            loaded_model=adapter.model_id() if adapter.is_loaded() else None,
            lifecycle_state=self.model_lifecycle,
            loaded=adapter.is_loaded(),
            warming=self.model_lifecycle == ModelLifecycleState.WARMING,
            last_load_started_at=self._last_load_started_at,
            last_load_completed_at=self._last_load_completed_at,
            last_unloaded_at=self._last_unloaded_at,
            last_error_code=self._last_error_code,
            last_error_message=self._last_error_message,
        )

    # ── Model lifecycle bookkeeping ────────────────────────────────────

    def begin_warmup(self) -> None:
        self.model_lifecycle = ModelLifecycleState.WARMING
        self._last_load_started_at = time.time()

    def complete_warmup(self) -> None:
        self.model_lifecycle = ModelLifecycleState.READY
        self._last_load_completed_at = time.time()
        self._last_error_code = None
        self._last_error_message = None

    def fail_warmup(self, *, error_code: Optional[str] = None, error_message: Optional[str] = None) -> None:
        self.model_lifecycle = ModelLifecycleState.FAILED
        self._last_error_code = error_code
        self._last_error_message = error_message

    def complete_unload(self) -> None:
        self.model_lifecycle = ModelLifecycleState.UNLOADED
        self._last_unloaded_at = time.time()

    # ── Admission counter helpers ──────────────────────────────────────

    def record_accepted(self) -> None:
        self.total_requests_accepted += 1

    def record_rejected(self, reason: str) -> None:
        self.total_requests_rejected += 1
        if reason == "rejected_overloaded":
            self.total_rejected_overloaded += 1
        elif reason == "rejected_prompt_too_large":
            self.total_rejected_prompt_too_large += 1
        elif reason == "rejected_too_many_messages":
            self.total_rejected_too_many_messages += 1
        elif reason == "rejected_max_tokens_too_high":
            self.total_rejected_max_tokens += 1
        elif reason == "rejected_model_not_ready":
            self.total_rejected_model_not_ready += 1

    def build_admission_config(self) -> dict:
        """Return current admission limits + counters."""
        from whooshd.config import (
            get_max_active_requests,
            get_max_messages,
            get_max_prompt_chars,
            get_max_request_max_tokens,
        )

        return {
            "max_active_requests": get_max_active_requests(),
            "active_jobs": self.active_jobs,
            "max_prompt_chars": get_max_prompt_chars(),
            "max_messages": get_max_messages(),
            "max_request_max_tokens": get_max_request_max_tokens(),
            "counters": {
                "accepted": self.total_requests_accepted,
                "rejected": self.total_requests_rejected,
                "rejected_overloaded": self.total_rejected_overloaded,
                "rejected_prompt_too_large": self.total_rejected_prompt_too_large,
                "rejected_too_many_messages": self.total_rejected_too_many_messages,
                "rejected_max_tokens": self.total_rejected_max_tokens,
                "rejected_model_not_ready": self.total_rejected_model_not_ready,
            },
        }

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        return self._models.get(model_id)

    # ── OpenAI-compatible model list ────────────────────────────────────

    def build_openai_model_list(self) -> OpenAIModelListResponse:
        """Return registered models in OpenAI /v1/models format."""
        entries: list[OpenAIModelEntry] = []
        for m in self._models.values():
            entries.append(
                OpenAIModelEntry(
                    id=m.id,
                    created=_STUB_MODEL_CREATED,
                    owned_by="whooshd",
                )
            )
        return OpenAIModelListResponse(data=entries)

    # ── Ollama-compatible tags ──────────────────────────────────────────

    def build_ollama_tags(self) -> OllamaTagsResponse:
        """Return registered models in Ollama /api/tags format."""
        entries: list[OllamaTagEntry] = []
        for m in self._models.values():
            entries.append(
                OllamaTagEntry(
                    name=f"{m.id}:latest",
                    modified_at="2024-01-01T00:00:00Z",
                    size=_STUB_MODEL_SIZE,
                )
            )
        return OllamaTagsResponse(models=entries)

    # ── Request lifecycle bookkeeping ───────────────────────────────────

    def begin_request(self, *, model: str, stream: bool) -> str:
        """Register a new request and return its ID.

        The caller is responsible for eventually calling complete_request,
        cancel_request, or fail_request.
        """
        request_id = str(uuid.uuid4())
        token = CancellationToken(request_id=request_id)
        self._requests[request_id] = _RequestRecord(
            request_id=request_id,
            model=model,
            stream=stream,
            status=RequestLifecycleState.ACCEPTED,
            cancel_token=token,
        )
        return request_id

    def get_cancellation_token(self, request_id: str) -> CancellationToken | None:
        """Return the cancellation token for an active request, or None."""
        rec = self._requests.get(request_id)
        return rec.cancel_token if rec else None

    def request_cancellation(self, request_id: str) -> bool:
        """Signal cancellation for an active request.  Returns True if signalled."""
        rec = self._requests.get(request_id)
        if rec is None:
            return False
        if rec.status in (
            RequestLifecycleState.COMPLETED,
            RequestLifecycleState.CANCELLED,
            RequestLifecycleState.FAILED,
        ):
            return False
        rec.cancel_requested = True
        self.total_requests_cancel_requested += 1
        if rec.cancel_token:
            rec.cancel_token.cancel()
        return True

    def mark_running(self, request_id: str) -> None:
        """Transition an accepted request to running."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.RUNNING

    def mark_streaming(self, request_id: str) -> None:
        """Transition a request to the streaming state."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.STREAMING

    def complete_request(self, request_id: str) -> None:
        """Mark a request as successfully completed."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.COMPLETED
            rec.ended_at = time.time()

    def cancel_request(self, request_id: str) -> None:
        """Mark a request as cancelled (client disconnect, timeout, etc.)."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.CANCELLED
            rec.ended_at = time.time()
            self.total_requests_cancelled += 1

    def record_stream_disconnect(self, request_id: str) -> None:
        """Record a client disconnect during streaming."""
        self.total_stream_disconnects += 1

    def fail_request(self, request_id: str, *, error_code: Optional[str] = None) -> None:
        """Mark a request as failed with an optional error code."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.FAILED
            rec.ended_at = time.time()
            rec.error_code = error_code

    # ── Request snapshot queries ────────────────────────────────────────

    def get_request_snapshot(self, request_id: str) -> Optional[RequestSnapshot]:
        """Return a public-safe snapshot for a single request, or None."""
        rec = self._requests.get(request_id)
        if rec is None:
            return None
        return RequestSnapshot(
            request_id=rec.request_id,
            model=rec.model,
            stream=rec.stream,
            status=rec.status,
            cancel_requested=rec.cancel_requested,
            started_at=rec.started_at,
            ended_at=rec.ended_at,
            error_code=rec.error_code,
        )

    def get_active_requests(self) -> list[RequestSnapshot]:
        """Return snapshots for all requests in non-terminal states."""
        return [
            RequestSnapshot(
                request_id=r.request_id,
                model=r.model,
                stream=r.stream,
                status=r.status,
                cancel_requested=r.cancel_requested,
                started_at=r.started_at,
                ended_at=r.ended_at,
                error_code=r.error_code,
            )
            for r in self._requests.values()
            if r.status
            in (
                RequestLifecycleState.ACCEPTED,
                RequestLifecycleState.RUNNING,
                RequestLifecycleState.STREAMING,
            )
        ]

    def get_all_requests(self) -> list[RequestSnapshot]:
        """Return snapshots for every tracked request (active + terminal)."""
        return [
            RequestSnapshot(
                request_id=r.request_id,
                model=r.model,
                stream=r.stream,
                status=r.status,
                cancel_requested=r.cancel_requested,
                started_at=r.started_at,
                ended_at=r.ended_at,
                error_code=r.error_code,
            )
            for r in self._requests.values()
        ]

    def build_request_list(self) -> RequestListResponse:
        """Build the GET /runtime/requests response payload."""
        snapshots = self.get_all_requests()
        return RequestListResponse(
            requests=snapshots,
            active_count=self.active_jobs,
        )


# Module-level singleton for the app layer to import.
_runtime: Optional[RuntimeState] = None


def get_runtime() -> RuntimeState:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeState()
    return _runtime

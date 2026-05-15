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
    ConcurrencyBudget,
    LoadedModelInfo,
    MemoryInfo,
    MemoryPressure,
    ModelCapability,
    ModelInfo,
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
        )

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
        self._requests[request_id] = _RequestRecord(
            request_id=request_id,
            model=model,
            stream=stream,
            status=RequestLifecycleState.ACCEPTED,
        )
        return request_id

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

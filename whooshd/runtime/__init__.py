"""Runtime state manager with request lifecycle tracking.

Owns:
  * runner status, memory, model registry (stubbed)
  * request lifecycle bookkeeping (begin / complete / cancel / fail)
  * active_jobs computed from live request state
  * snapshot APIs for /runtime and /runtime/requests
"""

from __future__ import annotations

import json
import os
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
from whooshd.config import (
    get_advertised_model_id,
    get_mlx_context_window,
    get_mlx_model_path,
    get_mlx_quantization,
    get_model_registry_path,
)

# Synthetic creation timestamp for inventory entries.
_STUB_MODEL_CREATED = 1700000000
# Synthetic size in bytes for inventory entries (~1.5 GB).
_STUB_MODEL_SIZE = 1_500_000_000
_DEFAULT_MODEL_CAPABILITIES = [
    ModelCapability.CHAT,
    ModelCapability.STREAMING,
    ModelCapability.JSON,
]


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

        # ── Queue counters ─────────────────────────────────────────────
        self.total_queued: int = 0
        self.total_dequeued: int = 0
        self.total_queue_rejected: int = 0
        self.total_queue_timeout: int = 0
        self.total_queue_cancelled: int = 0

        # ── Memory (stubbed) ────────────────────────────────────────────
        self.memory = MemoryInfo(
            pressure=MemoryPressure.NORMAL,
            total_gb=32.0,
            used_gb=4.2,
            available_gb=27.8,
        )

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

        self.active_model: Optional[str] = None

        # ── Model registry (loaded lazily or from YAML) ────────────────
        self._registry: object | None = None

    # ── Computed properties ─────────────────────────────────────────────

    @property
    def active_jobs(self) -> int:
        """Count of requests in non-terminal executing lifecycle states.

        Queued requests are excluded — they are not actively executing.
        """
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
    def queue_depth(self) -> int:
        """Count of requests currently in the queued state."""
        return sum(
            1
            for r in self._requests.values()
            if r.status == RequestLifecycleState.QUEUED
        )

    @property
    def oldest_queued_age_ms(self) -> float:
        """Age in milliseconds of the oldest queued request, or 0."""
        oldest: float | None = None
        now = time.time()
        for r in self._requests.values():
            if r.status == RequestLifecycleState.QUEUED:
                age = now - r.started_at
                if oldest is None or age > oldest:
                    oldest = age
        return (oldest * 1000.0) if oldest is not None else 0.0

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started_at

    # ── Queue lifecycle helpers ─────────────────────────────────────────

    def mark_queued(self, request_id: str) -> None:
        """Transition an accepted request to the queued state."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.QUEUED
            self.total_queued += 1

    def mark_dequeued(self, request_id: str) -> None:
        """Record that a request was removed from the queue for execution."""
        self.total_dequeued += 1

    def mark_timed_out(self, request_id: str) -> None:
        """Mark a queued request as timed out."""
        rec = self._requests.get(request_id)
        if rec:
            rec.status = RequestLifecycleState.TIMED_OUT
            rec.ended_at = time.time()
            self.total_queue_timeout += 1

    def record_queue_rejected(self) -> None:
        """Increment the queue-full rejection counter."""
        self.total_queue_rejected += 1

    def record_queue_cancelled(self) -> None:
        """Increment the queue cancellation counter."""
        self.total_queue_cancelled += 1

    # ── API builders ────────────────────────────────────────────────────

    def _build_model_info(self) -> ModelInfo:
        """Build the single advertised model entry from current config.

        Tries to read model metadata (context window, quantization) from
        the model's config.json when the model path is a local directory.
        Falls back to WHOOSHD_MLX_CONTEXT_WINDOW / WHOOSHD_MLX_QUANTIZATION
        env vars, then to sensible defaults.
        """
        from whooshd.routing import get_router

        router = get_router()
        model_id = get_advertised_model_id()
        # Check if any adapter has this model loaded.
        loaded = False
        for adapter in router._adapters.values():
            loaded_model_id = adapter.model_id()
            if adapter.is_loaded() and loaded_model_id == model_id:
                loaded = True
                break

        model_path = get_mlx_model_path()
        metadata = _read_model_metadata(model_path)

        context_window = metadata.get("context_window") or 32768
        quantization = metadata.get("quantization") or None
        memory_class = metadata.get("memory_class") or "small"
        max_concurrent = metadata.get("max_concurrent_jobs") or 2

        return ModelInfo(
            id=model_id,
            loaded=loaded,
            capabilities=list(_DEFAULT_MODEL_CAPABILITIES),
            max_concurrent_jobs=max_concurrent,
            context_window=context_window,
            quantization=quantization,
            memory_class=memory_class,
        )

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
        from whooshd.routing import get_router

        router = get_router()
        # Check if any adapter has a loaded model.
        loaded_model: str | None = None
        is_loaded = False
        for adapter in router._adapters.values():
            if adapter.is_loaded():
                loaded_model = adapter.model_id()
                is_loaded = True
                break
        return ModelRuntimeSnapshot(
            adapter=adapter_name,
            configured_model=configured_model,
            loaded_model=loaded_model,
            lifecycle_state=self.model_lifecycle,
            loaded=is_loaded,
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
            get_enable_queue,
            get_max_active_requests,
            get_max_queue_depth,
            get_max_messages,
            get_max_prompt_chars,
            get_max_request_max_tokens,
            get_queue_timeout_seconds,
        )

        return {
            "max_active_requests": get_max_active_requests(),
            "active_jobs": self.active_jobs,
            "max_prompt_chars": get_max_prompt_chars(),
            "max_messages": get_max_messages(),
            "max_request_max_tokens": get_max_request_max_tokens(),
            "queue_enabled": get_enable_queue(),
            "queue_depth": self.queue_depth,
            "max_queue_depth": get_max_queue_depth(),
            "queue_timeout_seconds": get_queue_timeout_seconds(),
            "counters": {
                "accepted": self.total_requests_accepted,
                "rejected": self.total_requests_rejected,
                "rejected_overloaded": self.total_rejected_overloaded,
                "rejected_prompt_too_large": self.total_rejected_prompt_too_large,
                "rejected_too_many_messages": self.total_rejected_too_many_messages,
                "rejected_max_tokens": self.total_rejected_max_tokens,
                "rejected_model_not_ready": self.total_rejected_model_not_ready,
                "queued": self.total_queued,
                "dequeued": self.total_dequeued,
                "queue_rejected": self.total_queue_rejected,
                "queue_timeout": self.total_queue_timeout,
                "queue_cancelled": self.total_queue_cancelled,
            },
        }

    # ── Model registry accessor ──────────────────────────────────────

    def _load_registry(self) -> object | None:
        """Load the model registry, or None if no registry file is present.

        The registry is loaded at most once and cached.
        """
        if self._registry is not None:
            return self._registry
        try:
            from whooshd.registry import load_model_registry

            explicit = get_model_registry_path()
            reg = load_model_registry(explicit)
            self._registry = reg
            return reg
        except Exception:
            # Swallow — registry is optional; fall back to env-var behaviour.
            self._registry = False  # sentinel: tried and not found
            return None

    def _has_registry(self) -> bool:
        """Return True if a model registry file was found and loaded."""
        reg = self._load_registry()
        return reg is not None and reg is not False and bool(reg)

    def list_models(self) -> list[ModelInfo]:
        """Synchronous model list (backward-compatible).

        Prefer ``list_models_async()`` in async contexts so adapter
        model lists can be fetched from live runtimes.
        """
        reg = self._load_registry()
        if reg and reg is not False and reg:
            return self._models_from_registry(reg)
        return [self._build_model_info()]

    async def list_models_async(self) -> list[ModelInfo]:
        """List models from the registry or from all registered adapters.

        When a registry is present, registry entries are authoritative.
        Otherwise each non-stub adapter contributes its configured model(s).
        """
        reg = self._load_registry()
        if reg and reg is not False and reg:
            return self._models_from_registry(reg)
        return await self._models_from_adapters()

    async def _models_from_adapters(self) -> list[ModelInfo]:
        """Build ModelInfo entries from all registered runtime adapters.

        Each non-stub adapter contributes one or more ModelInfo entries
        describing its configured model.  The stub adapter is excluded
        unless it is the only registered adapter.

        When only the stub adapter is registered, falls back to the
        legacy single-model behaviour driven by WHOOSHD_ADAPTER / WHOOSHD_MLX_MODEL.
        """
        from whooshd.routing import get_router

        router = get_router()
        results: list[ModelInfo] = []

        non_stub_adapters = [
            a for k, a in router._adapters.items() if k != "stub"
        ]

        if not non_stub_adapters:
            # No real runtimes registered — use legacy single-model path.
            return [self._build_model_info()]

        for adapter in non_stub_adapters:
            try:
                runtime_models = await adapter.list_models()
            except Exception:
                runtime_models = []

            for rm in runtime_models:
                loaded = adapter.is_loaded() if hasattr(adapter, "is_loaded") else False
                capabilities: list[ModelCapability] = list(_DEFAULT_MODEL_CAPABILITIES)

                if rm.supports_vision:
                    capabilities.append(ModelCapability.VISION)
                if rm.supports_reasoning:
                    capabilities.append(ModelCapability.REASONING)
                if rm.supports_tools:
                    capabilities.append(ModelCapability.TOOLS)

                results.append(ModelInfo(
                    id=rm.id,
                    loaded=loaded,
                    capabilities=capabilities,
                    max_concurrent_jobs=2,
                    context_window=rm.context_window or 32768,
                    quantization=None,
                    memory_class="small",
                ))

        if not results:
            return [self._build_model_info()]

        return results

    def _models_from_registry(self, registry: object) -> list[ModelInfo]:
        """Build ModelInfo entries from registry."""
        from whooshd.routing import get_router

        router = get_router()
        # Check if any adapter has a model loaded.
        loaded_model_id: str | None = None
        for adapter in router._adapters.values():
            if adapter.is_loaded():
                loaded_model_id = adapter.model_id()
                break
        results: list[ModelInfo] = []

        from whooshd.registry import ModelModality, RegistryModelEntry

        for model_id, entry in registry.enabled_models():
            loaded = bool(loaded_model_id and loaded_model_id == model_id)

            # Map registry modalities → ModelCapability enums.
            capabilities: list[ModelCapability] = []
            if ModelModality.TEXT in entry.modalities:
                capabilities.extend([
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.JSON,
                ])
            if ModelModality.EMBEDDING in entry.modalities:
                capabilities.append(ModelCapability.EMBEDDINGS)
            # Tools capability can be added later for tool-calling models.

            # Memory class heuristic based on model size tags.
            memory_class = "small"
            tags_lower = [t.lower() for t in entry.tags]
            if "large" in tags_lower or "70b" in tags_lower:
                memory_class = "large"
            elif "medium" in tags_lower or "30b" in tags_lower:
                memory_class = "medium"

            results.append(ModelInfo(
                id=model_id,
                loaded=loaded,
                capabilities=capabilities,
                max_concurrent_jobs=2,
                context_window=entry.context_window,
                quantization=None,  # registry does not carry quantization yet
                memory_class=memory_class,
            ))
        return results

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        for m in self.list_models():
            if m.id == model_id:
                return m
        return None

    # ── OpenAI-compatible model list ────────────────────────────────────

    async def build_openai_model_list(self) -> OpenAIModelListResponse:
        """Return registered models in OpenAI /v1/models format.

        When a registry is present, each entry includes engine/format/modality
        metadata.  Otherwise models are aggregated from all registered
        runtime adapters.

        Compatible registered models from the durable model-store are
        appended after built-in/static entries.
        """
        entries: list[OpenAIModelEntry] = []
        reg = self._load_registry()

        if reg and reg is not False and reg:
            for model_id, entry in reg.enabled_models():
                entries.append(
                    OpenAIModelEntry(
                        id=model_id,
                        created=_STUB_MODEL_CREATED,
                        owned_by="whooshd",
                        metadata={
                            "engine": entry.engine.value,
                            "format": entry.format.value,
                            "modalities": [m.value for m in entry.modalities],
                            "context_window": entry.context_window,
                            "display_name": entry.display_name,
                            "priority": entry.priority,
                            "warm_policy": entry.warm_policy.value,
                        },
                    )
                )
        else:
            # Aggregate from adapters.
            models = await self.list_models_async()
            for m in models:
                entries.append(
                    OpenAIModelEntry(
                        id=m.id,
                        created=_STUB_MODEL_CREATED,
                        owned_by="whooshd",
                    )
                )

        # ── Append compatible registered models from the model-store ──
        entries = await _append_registered_models_openai(entries)

        # ── Append external route models ──
        entries = await _append_external_models_openai(entries)

        return OpenAIModelListResponse(data=entries)

    # ── Ollama-compatible tags ──────────────────────────────────────────

    async def build_ollama_tags(self) -> OllamaTagsResponse:
        """Return registered models in Ollama /api/tags format.

        When a registry is present, each entry includes format/family details.
        Otherwise models are aggregated from all registered adapters.

        Compatible registered models from the durable model-store are
        appended after built-in/static entries.
        """
        entries: list[OllamaTagEntry] = []
        reg = self._load_registry()

        if reg and reg is not False and reg:
            for model_id, entry in reg.enabled_models():
                entries.append(
                    OllamaTagEntry(
                        name=model_id,
                        model=model_id,
                        modified_at="2024-01-01T00:00:00Z",
                        size=_STUB_MODEL_SIZE,
                        details={
                            "format": entry.format.value,
                            "family": entry.engine.value,
                            "context_window": entry.context_window,
                            "modalities": [m.value for m in entry.modalities],
                        },
                    )
                )
        else:
            for m in await self.list_models_async():
                entries.append(
                    OllamaTagEntry(
                        name=m.id,
                        model=m.id,
                        modified_at="2024-01-01T00:00:00Z",
                        size=_STUB_MODEL_SIZE,
                    )
                )

        # ── Append compatible registered models from the model-store ──
        entries = await _append_registered_models_ollama(entries)

        # ── Append external route models ──
        entries = await _append_external_models_ollama(entries)

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
            RequestLifecycleState.TIMED_OUT,
        ):
            return False
        rec.cancel_requested = True
        self.total_requests_cancel_requested += 1
        if rec.cancel_token:
            rec.cancel_token.cancel()
        return True

    def mark_running(self, request_id: str) -> None:
        """Transition an accepted or queued request to running."""
        rec = self._requests.get(request_id)
        if rec and rec.status in (
            RequestLifecycleState.ACCEPTED,
            RequestLifecycleState.QUEUED,
        ):
            rec.status = RequestLifecycleState.RUNNING

    def mark_streaming(self, request_id: str) -> None:
        """Transition an accepted or queued request to the streaming state."""
        rec = self._requests.get(request_id)
        if rec and rec.status in (
            RequestLifecycleState.ACCEPTED,
            RequestLifecycleState.QUEUED,
        ):
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
                RequestLifecycleState.QUEUED,
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


# ── Model metadata detection ────────────────────────────────────────────────


def _read_model_metadata(model_path: str) -> dict:
    """Try to read model metadata from config.json, env vars, and heuristics.

    Resolution order:
      1. WHOOSHD_MLX_CONTEXT_WINDOW / WHOOSHD_MLX_QUANTIZATION env vars
      2. config.json from the model directory (local paths only)
      3. Sensible defaults (context_window=32768, quantization=None)

    Returns a dict with keys: context_window, quantization, memory_class,
    max_concurrent_jobs.
    """
    metadata: dict = {}

    # ── Context window ─────────────────────────────────────────────────
    env_cw = get_mlx_context_window()
    if env_cw > 0:
        metadata["context_window"] = env_cw
    else:
        # Try config.json
        config = _try_read_model_config(model_path)
        if config:
            tc = config.get("text_config", {})
            cw = tc.get("max_position_embeddings") or config.get("max_position_embeddings")
            if cw:
                metadata["context_window"] = int(cw)

    # ── Quantization label ────────────────────────────────────────────
    env_q = get_mlx_quantization()
    if env_q:
        metadata["quantization"] = env_q
    else:
        config = _try_read_model_config(model_path)
        if config:
            qc = config.get("quantization", {})
            if isinstance(qc, dict):
                bits = qc.get("bits")
                mode = qc.get("mode")
                if bits:
                    label = f"{bits}bit"
                    if mode:
                        label += f"-{mode}"
                    metadata["quantization"] = label
            elif isinstance(qc, str):
                metadata["quantization"] = qc

    # ── Memory class (heuristic from file size) ────────────────────────
    metadata["memory_class"] = _guess_memory_class(model_path)

    # ── Max concurrent jobs (small models can handle more) ────────────
    metadata["max_concurrent_jobs"] = 2  # safe default; can be tuned later

    return metadata


def _try_read_model_config(model_path: str) -> dict | None:
    """Read config.json from a local model directory, or None."""
    if not model_path or not os.path.isdir(model_path):
        return None
    config_file = os.path.join(model_path, "config.json")
    if not os.path.isfile(config_file):
        return None
    try:
        with open(config_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _guess_memory_class(model_path: str) -> str:
    """Guess memory class from safetensors file size.

    small  — < 8 GB
    medium — 8–24 GB
    large  — > 24 GB
    """
    try:
        if os.path.isdir(model_path):
            total = 0
            for name in os.listdir(model_path):
                if name.endswith(".safetensors"):
                    total += os.path.getsize(os.path.join(model_path, name))
            gb = total / (1024 ** 3)
            if gb < 8:
                return "small"
            elif gb < 24:
                return "medium"
            else:
                return "large"
    except OSError:
        pass
    return "small"


# Module-level singleton for the app layer to import.
_runtime: Optional[RuntimeState] = None


def get_runtime() -> RuntimeState:
    global _runtime
    if _runtime is None:
        _runtime = RuntimeState()
    return _runtime


# ── Registered model inventory helpers ────────────────────────────────────


async def _append_registered_models_openai(
    entries: list[OpenAIModelEntry],
) -> list[OpenAIModelEntry]:
    """Append advertisable registered models from the model-store to an
    OpenAI-compatible model list.  Skips models whose id duplicates an
    existing built-in/static entry.
    """
    store_root = _get_store_root()
    if store_root is None:
        return entries

    from whooshd.model_registry.inventory import (
        collect_advertisable_registered_models,
    )

    existing_ids = {e.id for e in entries}

    try:
        registered = collect_advertisable_registered_models(store_root)
    except Exception:
        return entries

    for rm in registered:
        if rm.model_id in existing_ids:
            continue
        existing_ids.add(rm.model_id)

        meta: dict = {
            "engine": _adapter_to_engine(rm),
            "format": rm.detected_format,
            "modalities": list(rm.modalities),
            "display_name": rm.display_name or rm.model_id,
            "priority": "chat",
            "warm_policy": "warm_on_first_use",
            "source": "registered",
            "storage_mode": rm.storage_mode,
        }
        if rm.detected_family and rm.detected_family != "unknown":
            meta["family"] = rm.detected_family

        entries.append(
            OpenAIModelEntry(
                id=rm.model_id,
                created=_STUB_MODEL_CREATED,
                owned_by="whooshd",
                metadata=meta,
            )
        )

    return entries


async def _append_registered_models_ollama(
    entries: list[OllamaTagEntry],
) -> list[OllamaTagEntry]:
    """Append advertisable registered models from the model-store to an
    Ollama-compatible tag list.  Skips models whose name duplicates an
    existing built-in/static entry.
    """
    store_root = _get_store_root()
    if store_root is None:
        return entries

    from whooshd.model_registry.inventory import (
        collect_advertisable_registered_models,
    )

    existing_names = {e.name for e in entries}

    try:
        registered = collect_advertisable_registered_models(store_root)
    except Exception:
        return entries

    for rm in registered:
        if rm.model_id in existing_names:
            continue
        existing_names.add(rm.model_id)

        details: dict = {
            "format": rm.detected_format,
            "family": _adapter_to_engine(rm),
            "modalities": list(rm.modalities),
        }

        entries.append(
            OllamaTagEntry(
                name=rm.model_id,
                model=rm.model_id,
                modified_at="2024-01-01T00:00:00Z",
                size=_STUB_MODEL_SIZE,
                details=details,
            )
        )

    return entries


def _get_store_root() -> str | None:
    """Return the configured model-store root, or None."""
    from whooshd.config import get_model_store_root

    return get_model_store_root()


def _adapter_to_engine(rm) -> str:
    """Map a RegisteredModel's detected_format to an engine label."""
    fmt = rm.detected_format
    is_vision = "vision" in (rm.modalities or [])
    if fmt == "mlx":
        return "mlx_vlm" if is_vision else "mlx_lm_server"
    if fmt == "gguf":
        return "llama_cpp"
    return fmt


# ── External model inventory helpers ──────────────────────────────────────


async def _append_external_models_openai(
    entries: list[OpenAIModelEntry],
) -> list[OpenAIModelEntry]:
    """Append external route models to an OpenAI-compatible model list.

    Skips models whose id duplicates an existing entry (managed registry
    or built-in wins).
    """
    external = _get_external_inventory()
    if not external:
        return entries

    existing_ids = {e.id for e in entries}

    for ext in external:
        if ext.id in existing_ids:
            continue
        existing_ids.add(ext.id)

        meta: dict = {
            "source": "external",
            "registry_managed": False,
            "route_id": ext.route_id,
            "format": ext.format,
            "runtime": ext.runtime,
            "path_available": ext.path_available,
            "servable": ext.servable,
        }
        if ext.metadata.get("quant"):
            meta["quant"] = ext.metadata["quant"]

        entries.append(
            OpenAIModelEntry(
                id=ext.id,
                created=_STUB_MODEL_CREATED,
                owned_by="whooshd",
                metadata=meta,
            )
        )

    return entries


async def _append_external_models_ollama(
    entries: list[OllamaTagEntry],
) -> list[OllamaTagEntry]:
    """Append external route models to an Ollama-compatible tag list.

    Skips models whose name duplicates an existing entry (managed registry
    or built-in wins).
    """
    external = _get_external_inventory()
    if not external:
        return entries

    existing_names = {e.name for e in entries}

    for ext in external:
        if ext.id in existing_names:
            continue
        existing_names.add(ext.id)

        details: dict = {
            "source": "external",
            "registry_managed": False,
            "route_id": ext.route_id,
            "format": ext.format,
            "runtime": ext.runtime,
            "servable": ext.servable,
        }

        entries.append(
            OllamaTagEntry(
                name=ext.id,
                model=ext.id,
                modified_at="2024-01-01T00:00:00Z",
                size=_STUB_MODEL_SIZE,
                details=details,
            )
        )

    return entries


def _get_external_inventory() -> list:
    """Load external model inventory from configured external routes.

    Returns an empty list if no routes are configured, no routes are
    available, or if any error occurs during scanning.
    """
    try:
        from whooshd.models.routes import load_external_weight_routes
        from whooshd.models.inventory import list_external_model_inventory

        routes = load_external_weight_routes()
        if not routes:
            return []
        return list_external_model_inventory(routes)
    except Exception:
        return []

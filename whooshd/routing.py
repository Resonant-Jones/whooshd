"""Runtime router — maps model IDs to runtime backends.

The router holds multiple adapters (one per runtime kind) and:
  * dispatches inference requests to the correct backend
  * aggregates model inventory across all runtimes
  * reports per-runtime health

Routing policy (initial):
  1. If registry entry maps model → engine, route to matching runtime
  2. If model's format is gguf → route to llama_cpp
  3. If model's format is mlx → route to mlx_lm_server (or mlx_lm fallback)
  4. If ambiguous → return clear 400/404 error
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from whooshd.adapters.base import InferenceAdapter, StreamingNotSupportedError
from whooshd.backend_request_policy import (
    ensure_backend_chat_request,
    ensure_backend_generate_request,
)
from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    GenerateRequest,
    GenerateResponse,
    MultiRuntimeHealthResponse,
    RuntimeHealth,
    RuntimeKind,
    RuntimeModel,
    RuntimeProvenance,
)
from whooshd.log_safety import exception_metadata, safe_model_alias

logger = logging.getLogger(__name__)


_EXECUTION_MODES = {
    RuntimeKind.STUB.value: "stub",
    RuntimeKind.MLX_LM.value: "in_process",
    RuntimeKind.MLX_LM_SERVER.value: "managed_sidecar",
    RuntimeKind.MLX_VLM.value: "managed_sidecar",
    RuntimeKind.LLAMA_CPP.value: "external_sidecar",
}
_ADAPTER_NAMES = {
    RuntimeKind.STUB.value: "stub",
    RuntimeKind.MLX_LM.value: "mlx-lm",
    RuntimeKind.MLX_LM_SERVER.value: "mlx-lm-server",
    RuntimeKind.MLX_VLM.value: "mlx-vlm",
    RuntimeKind.LLAMA_CPP.value: "llama-cpp",
}


def inventory_provenance(
    *,
    model_id: str,
    runtime_kind: str,
    resolution_source: str,
    loaded: bool,
    adapter_name: str | None = None,
) -> RuntimeProvenance:
    """Build safe provenance for a model advertised by inventory."""
    from whooshd import __version__

    return RuntimeProvenance(
        requested_model_id=safe_model_alias(model_id),
        advertised_model_id=safe_model_alias(model_id),
        resolved_model_id=safe_model_alias(model_id),
        runtime_kind=str(runtime_kind)[:64],
        adapter_name=str(adapter_name or _ADAPTER_NAMES.get(runtime_kind, runtime_kind))[:64],
        resolution_source=resolution_source,
        execution_mode=_EXECUTION_MODES.get(runtime_kind, "external_sidecar"),
        model_lifecycle="ready" if loaded else "unloaded",
        whooshd_version=str(__version__)[:64],
    )


@dataclass(frozen=True)
class RuntimeResolution:
    """Authoritative route evidence retained through execution."""

    adapter: InferenceAdapter
    requested_model_id: str
    resolution_source: str
    advertised_model_id: str
    resolved_model_id: str
    execution_mode: str

    def for_model(self, model_id: str) -> "RuntimeResolution":
        """Retain the selected adapter while describing one batch member."""
        return RuntimeResolution(
            adapter=self.adapter,
            requested_model_id=model_id,
            resolution_source=self.resolution_source,
            advertised_model_id=model_id,
            resolved_model_id=self.resolved_model_id,
            execution_mode=self.execution_mode,
        )

    def provenance(
        self,
        *,
        request_id: str | None = None,
        backend_reported_model_id: str | None = None,
        streaming: bool = False,
        queued: bool = False,
        batched: bool = False,
        model_lifecycle=None,
    ) -> RuntimeProvenance:
        from whooshd import __version__

        return RuntimeProvenance(
            request_id=request_id,
            requested_model_id=safe_model_alias(self.requested_model_id),
            advertised_model_id=safe_model_alias(self.advertised_model_id),
            resolved_model_id=safe_model_alias(self.resolved_model_id),
            backend_reported_model_id=(
                safe_model_alias(backend_reported_model_id)
                if backend_reported_model_id
                else None
            ),
            runtime_kind=str(self.adapter.kind)[:64],
            adapter_name=str(self.adapter.name)[:64],
            resolution_source=self.resolution_source,
            execution_mode=self.execution_mode,
            streaming=bool(streaming),
            queued=bool(queued),
            batched=bool(batched),
            model_lifecycle=model_lifecycle,
            whooshd_version=str(__version__)[:64],
        )


class ModelResolutionError(Exception):
    """Raised when a requested model cannot be resolved to a runtime."""

    def __init__(self, model_id: str, detail: str = ""):
        self.model_id = model_id
        self.detail = detail
        super().__init__(
            f"Cannot resolve model '{safe_model_alias(model_id)}' to a runtime. "
            f"{detail}".strip()
        )


class RuntimeRouter:
    """Holds multiple runtime adapters and routes requests to the correct one.

    Usage::

        router = RuntimeRouter()
        router.register(llama_adapter)
        router.register(mlx_server_adapter)
        router.register(stub_adapter)

        # Route a chat completion
        response = await router.chat_completion(request)

        # Get model list across all runtimes
        models = await router.list_models()

        # Get per-runtime health
        health = await router.health()
    """

    def __init__(self) -> None:
        self._adapters: dict[str, InferenceAdapter] = {}
        # Mapping from model_id → adapter kind (runtime).
        self._model_runtime_map: dict[str, str] = {}

        # Stable process-lifetime session identity.
        import os, time as _time, uuid as _uuid
        self._session = {
            "pid": os.getpid(),
            "session_id": str(_uuid.uuid4())[:8],
            "started_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        }

    # ── Registration ────────────────────────────────────────────────────

    def register(self, adapter: InferenceAdapter) -> None:
        """Register an adapter.  Later registrations for the same kind
        replace earlier ones.
        """
        self._adapters[adapter.kind] = adapter
        logger.info("router.registered kind=%s name=%s", adapter.kind, adapter.name)

    def unregister(self, kind: str) -> None:
        """Remove a runtime by kind."""
        self._adapters.pop(kind, None)
        # Clear cached model→runtime mappings.
        self._model_runtime_map = {}

    def get_adapter(self, kind: str) -> InferenceAdapter | None:
        """Return the adapter for the given runtime kind, or None."""
        return self._adapters.get(kind)

    @property
    def registered_kinds(self) -> list[str]:
        return list(self._adapters.keys())

    # ── Model resolution ────────────────────────────────────────────────

    def _resolution(
        self,
        adapter: InferenceAdapter,
        model_id: str,
        source: str,
    ) -> RuntimeResolution:
        loaded_model_id = None
        try:
            loaded_model_id = adapter.model_id()
        except Exception:
            loaded_model_id = None
        return RuntimeResolution(
            adapter=adapter,
            requested_model_id=model_id,
            resolution_source=source,
            advertised_model_id=model_id,
            resolved_model_id=loaded_model_id or model_id,
            execution_mode=_EXECUTION_MODES.get(
                str(adapter.kind), "external_sidecar"
            ),
        )

    async def resolve_model_runtime(self, model_id: str) -> RuntimeResolution:
        """Resolve a model ID to its runtime adapter.

        Resolution order:
          1. Check the model registry for an engine→runtime mapping.
          2. Fall back to file extension heuristics (.gguf → llama_cpp).
          3. Check if any adapter already has this model loaded.
          4. Return 404-style error.

        Returns the adapter and bounded evidence that should serve the request.

        Raises ``ModelResolutionError`` if the model cannot be resolved.
        """
        # ── Step 1: Consult the model registry ─────────────────────
        # An explicitly configured registry is an operator-owned routing
        # boundary, not merely inventory metadata. Unknown and disabled model
        # IDs must not fall through to adapter heuristics or the single-runtime
        # compatibility fallback.
        from whooshd.config import get_model_registry_path

        authoritative_registry = bool(get_model_registry_path())
        reg = None
        try:
            from whooshd.runtime import get_runtime
            rt = get_runtime()
            reg = rt._load_registry()
        except Exception as exc:
            logger.warning(
                "router.registry_lookup_failed exception_type=%s",
                type(exc).__name__,
            )

        if authoritative_registry and (reg is None or reg is False):
            raise ModelResolutionError(
                model_id,
                "The configured runtime registry is unavailable.",
            )

        if reg is not None and reg is not False:
            entry = reg.get(model_id)
            if entry is None:
                if authoritative_registry:
                    raise ModelResolutionError(
                        model_id,
                        "Model is not allowed by the active runtime registry.",
                    )
            elif not entry.enabled:
                if authoritative_registry:
                    raise ModelResolutionError(
                        model_id,
                        "Model is disabled by the active runtime registry.",
                    )
            else:
                engine = entry.engine.value
                # Map registry engine → runtime kind
                engine_kind_map = {
                    "llama_cpp": RuntimeKind.LLAMA_CPP.value,
                    "mlx_lm": RuntimeKind.MLX_LM_SERVER.value,
                    "mlx_vlm": RuntimeKind.MLX_VLM.value,
                }
                kind = engine_kind_map.get(engine)
                if kind and kind in self._adapters:
                    return self._resolution(
                        self._adapters[kind], model_id, "authoritative_registry"
                    )
                if kind:
                    raise ModelResolutionError(
                        model_id,
                        f"Model '{model_id}' needs the '{kind}' runtime, "
                        f"which is not enabled. Available: {self.registered_kinds}",
                    )
                if authoritative_registry:
                    raise ModelResolutionError(
                        model_id,
                        "Model uses an unsupported runtime in the active registry.",
                    )

        # ── Step 1b: Check external route inventory ─────────────────
        external = await self._resolve_external_model(model_id)
        if external is not None:
            return self._resolution(external, model_id, "external_route")

        # ── Step 2: Heuristic by file extension ────────────────────
        if model_id.endswith(".gguf"):
            adapter = self._adapters.get(RuntimeKind.LLAMA_CPP.value)
            if adapter:
                return self._resolution(adapter, model_id, "format_heuristic")
            raise ModelResolutionError(
                model_id,
                "GGUF model detected but llama_cpp runtime is not registered.",
            )

        # ── Step 3: Check if any adapter has this model loaded ────
        for adapter in self._adapters.values():
            loaded_id = adapter.model_id()
            if loaded_id and loaded_id == model_id:
                return self._resolution(adapter, model_id, "loaded_model_match")

        # ── Step 3a: When WHOOSHD_ADAPTER=stub, prefer stub for
        #            unresolved model IDs (test/default posture). ──
        from whooshd.config import get_adapter_backend
        if get_adapter_backend() == "stub":
            stub_adapter = self._adapters.get(RuntimeKind.STUB.value)
            if stub_adapter is not None:
                return self._resolution(stub_adapter, model_id, "configured_stub")

        # ── Step 4: If there's exactly one enabled non-stub adapter,
        #            use it as default (backward compat).          ────
        non_stub = [
            a for k, a in self._adapters.items()
            if k != RuntimeKind.STUB.value
        ]
        if len(non_stub) == 1:
            return self._resolution(
                non_stub[0], model_id, "single_runtime_compatibility"
            )

        # ── Step 5: If stub is the only adapter, use it. ─────────
        stub = self._adapters.get(RuntimeKind.STUB.value)
        if stub and len(self._adapters) == 1:
            return self._resolution(stub, model_id, "stub_only_compatibility")

        raise ModelResolutionError(
            model_id,
            f"Model '{model_id}' does not match any registered runtime. "
            f"Available runtimes: {self.registered_kinds}",
        )

    async def _resolve_model_runtime(self, model_id: str) -> InferenceAdapter:
        """Backward-compatible adapter-only resolution helper."""
        return (await self.resolve_model_runtime(model_id)).adapter

    async def _resolve_external_model(
        self, model_id: str
    ) -> InferenceAdapter | None:
        """Try to resolve an external route model for runtime handoff.

        Checks external inventory, resolves the path, selects the adapter,
        and sets the external model path override on the adapter.

        Returns the adapter if resolved, or ``None`` if not an external model.
        Raises ``ModelResolutionError`` if found but not servable or route
        unavailable.
        """
        try:
            from whooshd.models.inventory import (
                resolve_external_runtime_model,
            )
            from whooshd.models.routes import load_external_weight_routes

            routes = load_external_weight_routes()
            if not routes:
                return None

            resolution = resolve_external_runtime_model(model_id, routes)
        except Exception:
            return None

        if not resolution.found:
            return None

        if not resolution.servable:
            reason = resolution.reason or "not_servable"
            if reason == "route_unavailable":
                raise ModelResolutionError(
                    model_id,
                    "External model route is unavailable.",
                )
            if reason == "not_servable":
                raise ModelResolutionError(
                    model_id,
                    "External model is visible but not servable by the current runtime configuration.",
                )
            raise ModelResolutionError(
                model_id,
                f"External model cannot be served: {reason}",
            )

        # ── Select the adapter ────────────────────────────────────
        runtime = resolution.runtime
        kind_map = {
            "llama_cpp": RuntimeKind.LLAMA_CPP.value,
            "mlx_lm": RuntimeKind.MLX_LM_SERVER.value,
        }
        kind = kind_map.get(runtime or "")
        if kind is None or kind not in self._adapters:
            raise ModelResolutionError(
                model_id,
                f"External model runtime '{runtime}' is not available.",
            )

        adapter = self._adapters[kind]

        # ── Set the external model path on the adapter ────────────
        if resolution.path and hasattr(adapter, "set_external_model_path"):
            adapter.set_external_model_path(resolution.path)

        return adapter

    # ── Inference dispatch ──────────────────────────────────────────────

    async def generate(
        self,
        request: GenerateRequest,
        *,
        request_id: str | None = None,
    ) -> GenerateResponse:
        """Route a generate request to the correct adapter."""
        model_id = request.model_id or "stub-model"
        resolution = await self.resolve_model_runtime(model_id)
        result = await resolution.adapter.generate(
            ensure_backend_generate_request(
                request, adapter_kind=resolution.adapter.kind
            )
        )
        provenance = resolution.provenance(
            request_id=request_id or request.request_id or result.request_id,
            backend_reported_model_id=result.model_id,
            streaming=False,
        )
        return result.model_copy(
            update={
                "runtime": result.runtime.model_copy(update={"provenance": provenance})
            }
        )

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        context=None,
    ) -> ChatCompletionResponse:
        """Route a chat completion request to the correct adapter."""
        resolution = await self.resolve_model_runtime(request.model)
        result = await resolution.adapter.chat_completion(
            ensure_backend_chat_request(
                request, adapter_kind=resolution.adapter.kind
            ),
            context=context,
        )
        return result.model_copy(
            update={
                "runtime_provenance": resolution.provenance(
                    backend_reported_model_id=result.model,
                    streaming=False,
                )
            }
        )

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        context=None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Route a streaming chat completion request to the correct adapter."""
        resolution = await self.resolve_model_runtime(request.model)
        if not resolution.adapter.supports_streaming:
            raise StreamingNotSupportedError(
                f"Runtime '{resolution.adapter.kind}' does not support streaming."
            )
        backend_request = ensure_backend_chat_request(
            request,
            adapter_kind=resolution.adapter.kind,
        )
        first = True
        async for chunk in resolution.adapter.chat_completion_stream(
            backend_request, context=context
        ):
            if first:
                first = False
                yield chunk.model_copy(
                    update={
                        "runtime_provenance": resolution.provenance(
                            backend_reported_model_id=chunk.model,
                            streaming=True,
                        )
                    }
                )
            else:
                yield chunk

    # ── Aggregated model inventory ──────────────────────────────────────

    async def list_models(self) -> list[RuntimeModel]:
        """Aggregate model lists from all registered runtime adapters.

        Each adapter returns its own models; the router concatenates them.
        """
        all_models: list[RuntimeModel] = []
        for adapter in self._adapters.values():
            try:
                models = await adapter.list_models()
                all_models.extend(models)
            except Exception as exc:
                logger.warning(
                    "router.list_models.error kind=%s error=%s",
                    adapter.kind,
                    exc,
                )
        return all_models

    # ── Aggregated health ───────────────────────────────────────────────

    async def health(self) -> MultiRuntimeHealthResponse:
        """Aggregate health from all registered runtime adapters."""
        runtimes: dict[str, RuntimeHealth] = {}
        aggregate_status = "ok"

        for adapter in self._adapters.values():
            try:
                h = await adapter.health()
                # Set configured_model from the adapter's model_id.
                configured = getattr(adapter, "model_id", lambda: None)()
                if configured and h.configured_model is None:
                    h.configured_model = configured
                runtimes[adapter.kind] = h
                if h.state.value in ("error", "degraded"):
                    if aggregate_status == "ok":
                        aggregate_status = "degraded"
            except Exception as exc:
                logger.warning(
                    "router.health.error kind=%s diagnostic=%s",
                    adapter.kind,
                    exception_metadata(exc),
                )
                runtimes[adapter.kind] = RuntimeHealth(
                    kind=adapter.kind,
                    enabled=True,
                    state="error",
                    detail=exception_metadata(exc),
                )
                aggregate_status = "degraded"

        # Session identity — stable across repeated calls.
        session = dict(self._session)
        session["registered_runtime_kinds"] = sorted(runtimes.keys())

        return MultiRuntimeHealthResponse(
            status=aggregate_status,
            runtimes=runtimes,
            session=session,
        )

    # ── Lifecycle helpers ───────────────────────────────────────────────

    async def warmup_all(self) -> dict[str, str]:
        """Trigger warmup on all registered adapters.

        Returns a dict of kind → status.
        """
        results: dict[str, str] = {}
        for adapter in self._adapters.values():
            try:
                await adapter.warmup()
                results[adapter.kind] = "ready"
            except Exception as exc:
                logger.warning(
                    "router.warmup.error kind=%s diagnostic=%s",
                    adapter.kind,
                    exception_metadata(exc),
                )
                results[adapter.kind] = (
                    f"failed: {type(exc).__name__}"
                )
        return results

    async def unload_all(self) -> dict[str, str]:
        """Trigger unload on all registered adapters."""
        results: dict[str, str] = {}
        for adapter in self._adapters.values():
            try:
                await adapter.unload()
                results[adapter.kind] = "unloaded"
            except Exception as exc:
                logger.warning(
                    "router.unload.error kind=%s diagnostic=%s",
                    adapter.kind,
                    exception_metadata(exc),
                )
                results[adapter.kind] = (
                    f"failed: {type(exc).__name__}"
                )
        return results


# Module-level singleton for the app layer.
_router: Optional[RuntimeRouter] = None


def get_router() -> RuntimeRouter:
    """Return the module-level router singleton."""
    global _router
    if _router is None:
        _router = RuntimeRouter()
    return _router


def reset_router() -> None:
    """Reset the router singleton (for tests)."""
    global _router
    _router = None

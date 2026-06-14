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
from typing import AsyncIterator, Optional

from whooshd.adapters.base import InferenceAdapter, StreamingNotSupportedError
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
)

logger = logging.getLogger(__name__)


class ModelResolutionError(Exception):
    """Raised when a requested model cannot be resolved to a runtime."""

    def __init__(self, model_id: str, detail: str = ""):
        self.model_id = model_id
        self.detail = detail
        super().__init__(f"Cannot resolve model '{model_id}' to a runtime. {detail}".strip())


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

    async def _resolve_model_runtime(self, model_id: str) -> InferenceAdapter:
        """Resolve a model ID to its runtime adapter.

        Resolution order:
          1. Check the model registry for an engine→runtime mapping.
          2. Fall back to file extension heuristics (.gguf → llama_cpp).
          3. Check if any adapter already has this model loaded.
          4. Return 404-style error.

        Returns the adapter that should serve the request.

        Raises ``ModelResolutionError`` if the model cannot be resolved.
        """
        # ── Step 1: Consult the model registry ─────────────────────
        try:
            from whooshd.runtime import get_runtime
            rt = get_runtime()
            reg = rt._load_registry()
            if reg and reg is not False and reg:
                entry = reg.get(model_id)
                if entry:
                    engine = entry.engine.value
                    # Map registry engine → runtime kind
                    engine_kind_map = {
                        "llama_cpp": RuntimeKind.LLAMA_CPP.value,
                        "mlx_lm": RuntimeKind.MLX_LM_SERVER.value,
                        "mlx_vlm": RuntimeKind.MLX_VLM.value,
                    }
                    kind = engine_kind_map.get(engine)
                    if kind and kind in self._adapters:
                        return self._adapters[kind]
                    # If the mapped kind isn't registered, fall through.
        except Exception:
            pass  # Registry lookup is best-effort.

        # ── Step 2: Heuristic by file extension ────────────────────
        if model_id.endswith(".gguf"):
            adapter = self._adapters.get(RuntimeKind.LLAMA_CPP.value)
            if adapter:
                return adapter
            raise ModelResolutionError(
                model_id,
                "GGUF model detected but llama_cpp runtime is not registered.",
            )

        # ── Step 3: Check if any adapter has this model loaded ────
        for adapter in self._adapters.values():
            loaded_id = adapter.model_id()
            if loaded_id and loaded_id == model_id:
                return adapter

        # ── Step 4: If there's exactly one enabled non-stub adapter,
        #            use it as default (backward compat).          ────
        non_stub = [
            a for k, a in self._adapters.items()
            if k != RuntimeKind.STUB.value
        ]
        if len(non_stub) == 1:
            return non_stub[0]

        # ── Step 5: If stub is the only adapter, use it. ─────────
        stub = self._adapters.get(RuntimeKind.STUB.value)
        if stub and len(self._adapters) == 1:
            return stub

        raise ModelResolutionError(
            model_id,
            f"Model '{model_id}' does not match any registered runtime. "
            f"Available runtimes: {self.registered_kinds}",
        )

    # ── Inference dispatch ──────────────────────────────────────────────

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Route a generate request to the correct adapter."""
        model_id = request.model_id or "stub-model"
        adapter = await self._resolve_model_runtime(model_id)
        return await adapter.generate(request)

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        context=None,
    ) -> ChatCompletionResponse:
        """Route a chat completion request to the correct adapter."""
        adapter = await self._resolve_model_runtime(request.model)
        return await adapter.chat_completion(request, context=context)

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        context=None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Route a streaming chat completion request to the correct adapter."""
        adapter = await self._resolve_model_runtime(request.model)
        if not adapter.supports_streaming:
            raise StreamingNotSupportedError(
                f"Runtime '{adapter.kind}' does not support streaming."
            )
        async for chunk in adapter.chat_completion_stream(request, context=context):
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
                    "router.health.error kind=%s error=%s",
                    adapter.kind,
                    exc,
                )
                runtimes[adapter.kind] = RuntimeHealth(
                    kind=adapter.kind,
                    enabled=True,
                    state="error",
                    detail=str(exc),
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
                    "router.warmup.error kind=%s error=%s",
                    adapter.kind,
                    exc,
                )
                results[adapter.kind] = f"failed: {exc}"
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
                    "router.unload.error kind=%s error=%s",
                    adapter.kind,
                    exc,
                )
                results[adapter.kind] = f"failed: {exc}"
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

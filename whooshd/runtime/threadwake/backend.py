"""KV-capable backend interface for ThreadWake Phase B+.

Defines the protocol that MLX, llama.cpp, and future backends implement
for KV cache reuse. Also provides a no-op adapter, a FakeKVBackend for
testing, and a registry so that unsupported backends degrade safely
without changing the generation path.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from .handles import KVCapability, KVHandle

logger = logging.getLogger(__name__)


# ── Protocol ────────────────────────────────────────────────────────────────


class KVCapableBackend(Protocol):
    """Protocol for backends that support KV cache operations.

    Every method is optional from a runtime perspective: callers MUST
    check ``supports_kv_cache()`` before invoking KV methods and MUST
    handle the case where the backend reports ``KVCapability.UNSUPPORTED``.
    """

    def supports_kv_cache(self) -> KVCapability:
        """Return the backend's KV capability level.

        Must never raise; return ``UNSUPPORTED`` for any backend that
        does not implement KV reuse.
        """
        ...

    def prefill_to_kv(
        self,
        tokens: list[int] | list[list[int]],
        *,
        model_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KVHandle:
        """Run prefill and return a KV handle.

        The backend MUST encode the resulting KV state into
        ``KVHandle.opaque_ref``. Callers MUST NOT inspect ``opaque_ref``.

        Requires at least ``PREFILL_ONLY`` capability.
        """
        ...

    def generate_from_kv(
        self,
        kv_handle: KVHandle,
        new_tokens: list[int],
        generation_params: dict[str, Any],
    ) -> Iterator[str]:
        """Generate tokens using an existing KV handle for the prefill.

        The backend reads ``opaque_ref`` to resume from cached state,
        then processes ``new_tokens`` as the decode phase.

        Requires at least ``RESUMABLE`` capability.
        """
        ...

    def clone_kv(self, kv_handle: KVHandle) -> KVHandle:
        """Return a deep copy of an existing KV handle.

        The new handle has a fresh id and timestamps but shares the
        same ``model_id`` and a backend-cloned ``opaque_ref``.

        Requires at least ``CLONEABLE`` capability.
        """
        ...

    def release_kv(self, kv_handle: KVHandle) -> None:
        """Release backend resources associated with a KV handle.

        After release, the handle is stale and MUST NOT be used for
        generation or cloning. Idempotent: releasing an already-released
        handle is safe.
        """
        ...


# ── No-op adapter ───────────────────────────────────────────────────────────


class NoOpKVBackendAdapter:
    """Safe no-op implementation for backends that do not support KV reuse.

    All KV methods raise ``RuntimeError`` with a clear message. Callers
    should route through the existing full-prefill path instead.

    ``supports_kv_cache()`` always returns ``UNSUPPORTED``.
    """

    def supports_kv_cache(self) -> KVCapability:
        return KVCapability.UNSUPPORTED

    def prefill_to_kv(
        self,
        tokens: list[int] | list[list[int]],
        *,
        model_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KVHandle:
        raise RuntimeError(
            "prefill_to_kv is not supported by this backend; "
            "use the standard full-prefill generation path"
        )

    def generate_from_kv(
        self,
        kv_handle: KVHandle,
        new_tokens: list[int],
        generation_params: dict[str, Any],
    ) -> Iterator[str]:
        raise RuntimeError(
            "generate_from_kv is not supported by this backend; "
            "use the standard generation path"
        )

    def clone_kv(self, kv_handle: KVHandle) -> KVHandle:
        raise RuntimeError(
            "clone_kv is not supported by this backend; "
            "use the standard full-prefill generation path"
        )

    def release_kv(self, kv_handle: KVHandle) -> None:
        """Safe no-op: releasing a handle on an unsupported backend is a no-op."""
        return None


# ── Fake KV backend (for testing) ──────────────────────────────────────────


@dataclass
class _FakeKVStore:
    """Internal per-model-id token storage for FakeKVBackend."""

    tokens: list[str] = field(default_factory=list)


class FakeKVBackend:
    """In-memory KV backend for testing ThreadWake reuse flows.

    Tokens are represented as ``list[str]`` — each string is one
    synthetic "token."  Generation output is deterministic based on
    the dynamic tail tokens and ``max_tokens`` parameter.

    This backend reports ``RESUMABLE`` capability and supports all
    KV operations (prefill, generate-from-kv, clone, release).
    """

    def __init__(self) -> None:
        self._store: dict[str, _FakeKVStore] = {}
        self.prefill_calls: list[dict[str, Any]] = []
        self.generate_from_kv_calls: list[dict[str, Any]] = []
        self.clone_calls: int = 0
        self.release_calls: int = 0

    def supports_kv_cache(self) -> KVCapability:
        return KVCapability.RESUMABLE

    def prefill_to_kv(
        self,
        tokens: list[str],
        *,
        model_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KVHandle:
        """Store tokens and return a KV handle."""
        self.prefill_calls.append({
            "token_count": len(tokens),
            "model_id": model_id,
            "metadata": metadata,
        })
        store = _FakeKVStore(tokens=list(tokens))
        store_key = f"{model_id}:prefill"
        self._store[store_key] = store

        return KVHandle(
            backend="fake",
            model_id=model_id,
            token_count=len(tokens),
            scope="thread",
            metadata=metadata or {},
            opaque_ref={"store_key": store_key, "model_id": model_id},
        )

    def generate_from_kv(
        self,
        kv_handle: KVHandle,
        new_tokens: list[str],
        generation_params: dict[str, Any],
    ) -> Iterator[str]:
        """Generate deterministic output from KV state + new tokens.

        Output is ``["tk_0", "tk_1", ...]`` up to ``max_tokens``.
        """
        opaque = kv_handle.opaque_ref or {}
        store_key = opaque.get("store_key", "")
        store = self._store.get(store_key)
        prefix_len = len(store.tokens) if store else 0

        self.generate_from_kv_calls.append({
            "prefix_token_count": prefix_len,
            "new_token_count": len(new_tokens),
            "params": generation_params,
        })

        max_tokens = generation_params.get("max_tokens", 16)
        for i in range(min(max_tokens, 256)):
            yield f"tk_{i}"

    def clone_kv(self, kv_handle: KVHandle) -> KVHandle:
        """Return a deep copy of the handle."""
        self.clone_calls += 1
        opaque = kv_handle.opaque_ref
        cloned_opaque = copy.deepcopy(opaque) if opaque else None
        return KVHandle(
            backend=kv_handle.backend,
            model_id=kv_handle.model_id,
            token_count=kv_handle.token_count,
            scope=kv_handle.scope,
            metadata=dict(kv_handle.metadata),
            opaque_ref=cloned_opaque,
        )

    def release_kv(self, kv_handle: KVHandle) -> None:
        """Release stored KV resources."""
        self.release_calls += 1
        opaque = kv_handle.opaque_ref or {}
        store_key = opaque.get("store_key", "")
        self._store.pop(store_key, None)

    def reset(self) -> None:
        """Clear all state (for test isolation)."""
        self._store.clear()
        self.prefill_calls.clear()
        self.generate_from_kv_calls.clear()
        self.clone_calls = 0
        self.release_calls = 0


# ── Registry ────────────────────────────────────────────────────────────────


class BackendKVAdapterRegistry:
    """Registry mapping backend names to KVCapableBackend instances.

    Unregistered backends automatically receive the no-op adapter, so
    callers never encounter a ``None`` adapter.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, KVCapableBackend] = {}
        self._noop = NoOpKVBackendAdapter()

    def register(self, backend: str, adapter: KVCapableBackend) -> None:
        """Register a KV-capable adapter for a named backend.

        Replaces any existing registration for the same backend name.
        """
        self._adapters[backend] = adapter
        logger.debug(
            "BackendKVAdapterRegistry: registered %s -> %s",
            backend,
            type(adapter).__name__,
        )

    def get(self, backend: str) -> KVCapableBackend:
        """Return the KV adapter for a backend, or the no-op fallback."""
        return self._adapters.get(backend, self._noop)

    def capability(self, backend: str) -> KVCapability:
        """Return the capability for a backend (safe shortcut)."""
        return self.get(backend).supports_kv_cache()

    def is_kv_capable(self, backend: str) -> bool:
        """Return True if the backend supports any KV reuse level."""
        return self.capability(backend) != KVCapability.UNSUPPORTED

    def registered_backends(self) -> list[str]:
        """Return backend names with non-noop adapters registered."""
        return sorted(self._adapters.keys())

    def clear(self) -> None:
        """Remove all registrations (for test reset)."""
        self._adapters.clear()

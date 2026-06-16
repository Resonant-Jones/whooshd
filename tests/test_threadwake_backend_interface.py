"""Tests for ThreadWake Phase B backend KV interface, no-op adapter, and registry."""

from __future__ import annotations

import pytest

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import (
    BackendKVAdapterRegistry,
    NoOpKVBackendAdapter,
)
from whooshd.runtime.threadwake.handles import KVCapability, KVHandle
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


# ── helpers ────────────────────────────────────────────────────────────────


def _request(**overrides) -> ChatCompletionRequest:
    data = {
        "model": "stub-model",
        "messages": [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "Latest prompt"},
        ],
        "threadwake": {
            "enabled": True,
            "mode": "observe",
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        },
    }
    data.update(overrides)
    return ChatCompletionRequest.model_validate(data)


# ── NoOpKVBackendAdapter ───────────────────────────────────────────────────


class TestNoOpKVBackendAdapter:
    def test_supports_kv_cache_returns_unsupported(self):
        adapter = NoOpKVBackendAdapter()
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED

    def test_prefill_to_kv_raises_runtime_error(self):
        adapter = NoOpKVBackendAdapter()
        with pytest.raises(RuntimeError, match="prefill_to_kv is not supported"):
            adapter.prefill_to_kv([1, 2, 3], model_id="m")

    def test_generate_from_kv_raises_runtime_error(self):
        adapter = NoOpKVBackendAdapter()
        handle = KVHandle(backend="stub", model_id="m")
        with pytest.raises(RuntimeError, match="generate_from_kv is not supported"):
            next(adapter.generate_from_kv(handle, [4, 5, 6], {}))

    def test_clone_kv_raises_runtime_error(self):
        adapter = NoOpKVBackendAdapter()
        handle = KVHandle(backend="stub", model_id="m")
        with pytest.raises(RuntimeError, match="clone_kv is not supported"):
            adapter.clone_kv(handle)

    def test_release_kv_is_safe_noop(self):
        adapter = NoOpKVBackendAdapter()
        handle = KVHandle(backend="stub", model_id="m")
        # Should not raise
        result = adapter.release_kv(handle)
        assert result is None

    def test_release_kv_twice_is_idempotent(self):
        adapter = NoOpKVBackendAdapter()
        handle = KVHandle(backend="stub", model_id="m")
        adapter.release_kv(handle)
        adapter.release_kv(handle)  # Should not raise


# ── BackendKVAdapterRegistry ────────────────────────────────────────────────


class TestBackendKVAdapterRegistry:
    def test_unregistered_backend_returns_noop(self):
        reg = BackendKVAdapterRegistry()
        adapter = reg.get("nonexistent")
        assert isinstance(adapter, NoOpKVBackendAdapter)
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED

    def test_unsupported_backend_reports_unsupported(self):
        reg = BackendKVAdapterRegistry()
        assert reg.capability("nonexistent") == KVCapability.UNSUPPORTED

    def test_is_kv_capable_returns_false_for_unsupported(self):
        reg = BackendKVAdapterRegistry()
        assert reg.is_kv_capable("nonexistent") is False

    def test_register_and_retrieve_adapter(self):
        reg = BackendKVAdapterRegistry()

        class FakeCapableBackend:
            def supports_kv_cache(self):
                return KVCapability.RESUMABLE

            def prefill_to_kv(self, tokens, *, model_id, metadata=None):
                return KVHandle(
                    backend="fake",
                    model_id=model_id,
                    token_count=len(tokens) if isinstance(tokens, list) else 0,
                    opaque_ref={"kv": "state"},
                )

            def generate_from_kv(self, kv_handle, new_tokens, generation_params):
                yield "generated"

            def clone_kv(self, kv_handle):
                return KVHandle(backend="fake", model_id=kv_handle.model_id)

            def release_kv(self, kv_handle):
                pass

        reg.register("fake", FakeCapableBackend())
        assert reg.is_kv_capable("fake") is True
        assert reg.capability("fake") == KVCapability.RESUMABLE

        adapter = reg.get("fake")
        assert adapter.supports_kv_cache() == KVCapability.RESUMABLE

    def test_reregister_replaces_previous(self):
        reg = BackendKVAdapterRegistry()
        first = NoOpKVBackendAdapter()
        reg.register("test", first)
        reg.register("test", NoOpKVBackendAdapter())
        assert reg.get("test") is not first

    def test_registered_backends_lists_keys(self):
        reg = BackendKVAdapterRegistry()
        reg.register("mlx", NoOpKVBackendAdapter())
        reg.register("llama_cpp", NoOpKVBackendAdapter())
        assert set(reg.registered_backends()) == {"llama_cpp", "mlx"}

    def test_clear_removes_all_registrations(self):
        reg = BackendKVAdapterRegistry()
        reg.register("mlx", NoOpKVBackendAdapter())
        reg.clear()
        assert reg.registered_backends() == []
        assert isinstance(reg.get("mlx"), NoOpKVBackendAdapter)

    def test_missing_backend_adapter_degrades_safely(self):
        """A missing backend adapter should not cause errors — must degrade to noop."""
        reg = BackendKVAdapterRegistry()
        # No registration for "unknown_backend"
        adapter = reg.get("unknown_backend")
        assert adapter is not None
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED
        # release is safe
        adapter.release_kv(KVHandle(backend="unknown_backend", model_id="m"))


# ── ThreadWakeManager backend capability reporting ──────────────────────────


class TestManagerBackendCapabilityReporting:
    def test_unsupported_backend_reports_in_observation(self):
        reg = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        observation = mgr.observe_request(_request(), backend="stub")

        assert observation.backend_kv_capability == "unsupported"
        assert observation.can_reuse_kv is False
        assert observation.kv_reuse_reason == "backend_unsupported"

    def test_backend_unknown_reports_none_capability(self):
        reg = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        observation = mgr.observe_request(_request(), backend=None)

        assert observation.backend_kv_capability is None
        assert observation.can_reuse_kv is False
        assert observation.kv_reuse_reason == "backend_unknown"

    def test_unsupported_backend_does_not_fail_observe_mode(self):
        """Observe mode must complete even for unsupported backends."""
        reg = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        observation = mgr.observe_request(_request(), backend="stub")

        assert observation.enabled is True
        assert observation.mode.value == "observe"
        assert observation.eligible is True  # prompt itself is eligible
        assert observation.can_reuse_kv is False  # but backend is unsupported

    def test_registered_resumable_backend_eligible_reports_observe_mode_limit(self):
        reg = BackendKVAdapterRegistry()

        class ResumableBackend:
            def supports_kv_cache(self):
                return KVCapability.RESUMABLE

        reg.register("resumable_mlx", ResumableBackend())
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        observation = mgr.observe_request(_request(), backend="resumable_mlx")

        assert observation.backend_kv_capability == "resumable"
        assert observation.can_reuse_kv is False  # Phase B observe only
        assert observation.kv_reuse_reason == "observe_mode_not_reusing"

    def test_registered_backend_ineligible_prompt_reports_capable_but_ineligible(self):
        reg = BackendKVAdapterRegistry()

        class ResumableBackend:
            def supports_kv_cache(self):
                return KVCapability.RESUMABLE

        reg.register("resumable_mlx", ResumableBackend())
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        # High min token threshold makes short stable prefix ineligible
        req = _request(threadwake={
            "enabled": True,
            "mode": "observe",
            "scope": "thread",
            "min_stable_prefix_tokens": 999999,
        })

        observation = mgr.observe_request(req, backend="resumable_mlx")

        assert observation.backend_kv_capability == "resumable"
        assert observation.can_reuse_kv is False
        assert observation.eligible is False
        assert "backend_capable_but_ineligible" in observation.kv_reuse_reason

    def test_disabled_request_still_reports_backend_capability(self):
        reg = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        req = _request(threadwake={"enabled": False, "mode": "off"})
        observation = mgr.observe_request(req, backend="stub")

        assert observation.enabled is False
        assert observation.eligible is False
        assert observation.backend_kv_capability == "unsupported"
        assert observation.can_reuse_kv is False

    def test_generation_path_is_unchanged(self):
        """Phase B must not alter the generation path — observe only."""
        reg = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), backend_registry=reg)

        req = _request()
        observation = mgr.observe_request(req, backend="stub")

        # Confirm this is still observe-mode only — no KV was stored
        assert observation.cache_hit is False
        assert observation.mode.value == "observe"
        assert observation.can_reuse_kv is False

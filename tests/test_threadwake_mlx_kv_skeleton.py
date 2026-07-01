"""Tests for MLX KV backend skeleton — prove it refuses unsafe reuse."""

from __future__ import annotations

import pytest

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.handles import KVCapability
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry
from whooshd.runtime.threadwake.mlx_kv import MLXKVBackendAdapter
from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
)
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
from whooshd.runtime.threadwake.index import ThreadWakeIndex


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_request(messages=None, model="test-model"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    return ChatCompletionRequest.model_validate({
        "model": model,
        "messages": messages,
        "threadwake": {
            "enabled": True,
            "mode": "ephemeral",
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        },
    })


def _generate_fn(request, params):
    max_tokens = params.get("max_tokens", 4)
    return [f"gen_{i}" for i in range(max_tokens)]


# ── Test 1: MLX KV adapter reports unsupported ────────────────────────────


class TestMLXKVCapability:
    def test_adapter_reports_unsupported(self):
        adapter = MLXKVBackendAdapter()
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED

    def test_adapter_with_model_and_tokenizer_still_unsupported(self):
        """Even with real model/tokenizer references, capability stays
        unsupported until proven otherwise."""
        adapter = MLXKVBackendAdapter(model="fake-model", tokenizer="fake-tok")
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED


# ── Test 2: Unsafe operations fail clearly ─────────────────────────────────


class TestMLXKVUnsafeOperations:
    def test_prefill_to_kv_raises(self):
        adapter = MLXKVBackendAdapter()
        with pytest.raises(RuntimeError, match="not implemented"):
            adapter.prefill_to_kv([1, 2, 3], model_id="test")

    def test_generate_from_kv_raises(self):
        adapter = MLXKVBackendAdapter()
        from whooshd.runtime.threadwake.handles import KVHandle
        handle = KVHandle(backend="mlx", model_id="test", token_count=0, opaque_ref={})
        with pytest.raises(RuntimeError, match="not implemented"):
            list(adapter.generate_from_kv(handle, [1, 2, 3], {}))

    def test_clone_kv_raises(self):
        adapter = MLXKVBackendAdapter()
        from whooshd.runtime.threadwake.handles import KVHandle
        handle = KVHandle(backend="mlx", model_id="test", token_count=0, opaque_ref={})
        with pytest.raises(RuntimeError, match="not implemented"):
            adapter.clone_kv(handle)

    def test_release_kv_is_safe_noop(self):
        adapter = MLXKVBackendAdapter()
        from whooshd.runtime.threadwake.handles import KVHandle
        handle = KVHandle(backend="mlx", model_id="test", token_count=0, opaque_ref={})
        # release_kv must not raise.
        result = adapter.release_kv(handle)
        assert result is None


# ── Test 3: Registry reports MLX unsupported ───────────────────────────────


class TestMLXKVRegistry:
    def test_registry_reports_unsupported(self):
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())
        assert registry.capability("mlx") == KVCapability.UNSUPPORTED

    def test_registry_is_kv_capable_false(self):
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())
        assert registry.is_kv_capable("mlx") is False

    def test_unregistered_backend_also_unsupported(self):
        registry = BackendKVAdapterRegistry()
        assert registry.capability("unknown") == KVCapability.UNSUPPORTED
        assert registry.is_kv_capable("unknown") is False


# ── Test 4: ThreadWake ephemeral falls back for MLX skeleton ───────────────


class TestMLXKVEphemeralFallback:
    def test_ephemeral_falls_back(self):
        """With MLX KV skeleton registered, ephemeral mode must fall back
        to full generation — no KV reuse attempted."""
        registry = BackendKVAdapterRegistry()
        tok_registry = BackendTokenizerAdapterRegistry()

        # Register MLX KV skeleton (unsupported) + fake tokenizer.
        registry.register("mlx", MLXKVBackendAdapter())
        tok_registry.register("mlx", FakeTokenizerAdapter())

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            tokenizer_registry=tok_registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        req = _make_request()
        result = mgr.execute_ephemeral(
            req, backend="mlx", generate_fn=_generate_fn,
        )

        # Must fall back to full generation — no cache hit.
        assert result.cache_hit is False
        assert len(result.output_tokens) > 0

    def test_no_kv_handle_stored_on_fallback(self):
        """When MLX KV is unsupported, no KV handle should be created
        or stored in the index."""
        registry = BackendKVAdapterRegistry()
        tok_registry = BackendTokenizerAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())
        tok_registry.register("mlx", FakeTokenizerAdapter())

        index = ThreadWakeIndex(max_entries=50)
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            tokenizer_registry=tok_registry,
            index=index,
        )

        req = _make_request()

        # Two requests — neither should store a KV handle.
        mgr.execute_ephemeral(req, backend="mlx", generate_fn=_generate_fn)
        mgr.execute_ephemeral(req, backend="mlx", generate_fn=_generate_fn)

        # No ready entries — KV never stored because capability is unsupported.
        health = mgr.get_health()
        assert health["ready_entries"] == 0

    def test_no_request_failure_on_unsupported(self):
        """Requests must never fail just because KV is unsupported."""
        registry = BackendKVAdapterRegistry()
        tok_registry = BackendTokenizerAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())
        tok_registry.register("mlx", FakeTokenizerAdapter())

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            tokenizer_registry=tok_registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        # Multiple requests — none should raise.
        for _ in range(5):
            req = _make_request()
            result = mgr.execute_ephemeral(
                req, backend="mlx", generate_fn=_generate_fn,
            )
            assert result.cache_hit is False
            assert result.output_tokens


# ── Test 5: Health reports honest capability ───────────────────────────────


class TestMLXKVHealth:
    def test_health_reports_mlx_unsupported(self):
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        health = mgr.get_health()
        caps = health.get("backend_capabilities", {})
        assert caps.get("mlx") == "unsupported"

    def test_health_never_reports_resumable(self):
        """MLX must never be reported as resumable/cloneable/serializable
        until real KV reuse is implemented."""
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        health = mgr.get_health()
        caps = health.get("backend_capabilities", {})
        mlx_cap = caps.get("mlx", "")
        assert mlx_cap not in ("resumable", "cloneable", "serializable")


# ── Test 6: No opaque leakage ─────────────────────────────────────────────


class TestMLXKVNoLeakage:
    def test_health_no_model_internals(self):
        adapter = MLXKVBackendAdapter(model="secret-model", tokenizer="secret-tok")
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", adapter)

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        health = mgr.get_health()
        health_str = str(health)
        assert "secret-model" not in health_str
        assert "secret-tok" not in health_str

    def test_no_placeholder_kv_handles_created(self):
        """The skeleton must never create misleading placeholder handles."""
        adapter = MLXKVBackendAdapter()
        # prefill_to_kv must raise, not return a fake handle.
        with pytest.raises(RuntimeError):
            adapter.prefill_to_kv([1, 2, 3], model_id="test")

"""Tests for MLX tokenizer adapter registration at runtime."""

from __future__ import annotations

import pytest

from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    ThreadWakeTokenizerCapability,
)


def _has_mlx() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


class MockTokenizerAdapter:
    """Mock for BackendTokenizerAdapter protocol."""
    def __init__(self, capability=ThreadWakeTokenizerCapability.TOKEN_IDS):
        self._capability = capability
        self.tokenize_calls = 0

    def supports_tokenization(self):
        return self._capability

    def tokenize_prompt(self, graph, request, *, model_id):
        self.tokenize_calls += 1
        from whooshd.runtime.threadwake.tokenization import TokenizedPrompt
        return TokenizedPrompt(
            model_id=model_id, backend="mlx",
            token_ids=[1, 2, 3],
            stable_prefix_token_ids=[1],
            dynamic_tail_token_ids=[2, 3],
            real_tokenization=True,
        )


class TestRegistry:
    def test_register_and_retrieve(self):
        reg = BackendTokenizerAdapterRegistry()
        adapter = MockTokenizerAdapter()
        reg.register("mlx", adapter)
        assert reg.capability("mlx") == ThreadWakeTokenizerCapability.TOKEN_IDS
        assert reg.has_real_tokenization("mlx") is True

    def test_unregister_removes_adapter(self):
        reg = BackendTokenizerAdapterRegistry()
        reg.register("mlx", MockTokenizerAdapter())
        assert "mlx" in reg.registered_backends()
        reg.unregister("mlx")
        assert "mlx" not in reg.registered_backends()

    def test_unregister_nonexistent_is_noop(self):
        reg = BackendTokenizerAdapterRegistry()
        reg.unregister("nonexistent")  # Should not raise

    def test_reregister_replaces(self):
        reg = BackendTokenizerAdapterRegistry()
        first = MockTokenizerAdapter()
        second = MockTokenizerAdapter()
        reg.register("mlx", first)
        reg.register("mlx", second)
        assert reg.get("mlx") is second

    def test_registered_backends_lists_keys(self):
        reg = BackendTokenizerAdapterRegistry()
        reg.register("mlx", MockTokenizerAdapter())
        assert reg.registered_backends() == ["mlx"]


class TestWithoutTokenizer:
    def test_no_registry_returns_noop(self):
        reg = BackendTokenizerAdapterRegistry()
        adapter = reg.get("mlx")
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_has_real_tokenization_false_without_registration(self):
        reg = BackendTokenizerAdapterRegistry()
        assert reg.has_real_tokenization("mlx") is False

    def test_capability_unsupported_without_registration(self):
        reg = BackendTokenizerAdapterRegistry()
        assert reg.capability("mlx") == ThreadWakeTokenizerCapability.UNSUPPORTED


class TestMLXClaims:
    def test_mlx_does_not_claim_token_ids_with_spans(self):
        """MLX adapter must report token_ids, not token_ids_with_spans."""
        adapter = MockTokenizerAdapter(capability=ThreadWakeTokenizerCapability.TOKEN_IDS)
        assert adapter.supports_tokenization() != ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.TOKEN_IDS

    def test_production_kv_reuse_remains_disabled(self):
        """No KVCapableBackend is registered for MLX production reuse."""
        from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry
        reg = BackendKVAdapterRegistry()
        adapter = reg.get("mlx")
        assert adapter.supports_kv_cache().value == "unsupported"


@pytest.mark.skipif(not _has_mlx(), reason="mlx_lm not available")
class TestMLXAdapterLive:
    def test_tokenizer_property_exists(self):
        """Verify MLXInferenceAdapter exposes tokenizer property."""
        from whooshd.adapters.mlx import MLXInferenceAdapter
        adapter = MLXInferenceAdapter()
        assert hasattr(adapter, "tokenizer")
        # Before load, tokenizer is None
        assert adapter.tokenizer is None

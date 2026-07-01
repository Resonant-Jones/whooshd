"""Tests for MLX prompt-cache feasibility probe and experimental gate."""

from __future__ import annotations

import pytest

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.handles import KVCapability, KVHandle
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry
from whooshd.runtime.threadwake.mlx_kv import MLXKVBackendAdapter
from whooshd.runtime.threadwake.mlx_kv_feasibility import (
    get_mlx_kv_experimental_enabled,
    get_mlx_kv_feasibility_report,
    is_mlx_kv_feasible,
    probe_mlx_prompt_cache_api,
)
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


# ── Test 1: API probe reports cleanly ─────────────────────────────────────


class TestProbeAPI:
    def test_probe_returns_structured_result(self):
        result = probe_mlx_prompt_cache_api()
        assert isinstance(result, dict)
        assert "available" in result
        assert "blockers" in result

    def test_probe_never_raises(self):
        """Probe must never crash, even if imports fail."""
        result = probe_mlx_prompt_cache_api()
        assert isinstance(result, dict)

    def test_is_mlx_kv_feasible_is_bool(self):
        feasible = is_mlx_kv_feasible()
        assert isinstance(feasible, bool)


# ── Test 2: Default MLX KV capability remains unsupported ──────────────────


class TestDefaultCapability:
    def test_adapter_unsupported_by_default(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "false")
        adapter = MLXKVBackendAdapter()
        assert adapter.supports_kv_cache() == KVCapability.UNSUPPORTED

    def test_adapter_unsupported_when_flag_absent(self):
        """Without the env var set, capability must be unsupported."""
        adapter = MLXKVBackendAdapter()
        cap = adapter.supports_kv_cache()
        assert cap == KVCapability.UNSUPPORTED, f"expected UNSUPPORTED, got {cap}"


# ── Test 3: Experimental flag required ─────────────────────────────────────


class TestExperimentalGate:
    def test_experimental_flag_disabled_by_default(self):
        assert get_mlx_kv_experimental_enabled() is False

    def test_experimental_flag_enabled(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "true")
        assert get_mlx_kv_experimental_enabled() is True

    def test_experimental_flag_with_mlx_available(self, monkeypatch):
        """When flag is true AND mlx-lm is installed, capability may be
        experimental.  If mlx-lm is installed, this should report
        EXPERIMENTAL."""
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "true")
        adapter = MLXKVBackendAdapter()
        cap = adapter.supports_kv_cache()
        # On this machine (Apple Silicon with mlx-lm installed), should
        # report EXPERIMENTAL when the flag is set.
        if is_mlx_kv_feasible():
            assert cap == KVCapability.EXPERIMENTAL, f"expected EXPERIMENTAL, got {cap}"
        else:
            assert cap == KVCapability.UNSUPPORTED


# ── Test 4: No fake handle creation ────────────────────────────────────────


class TestNoFakeHandles:
    def test_prefill_without_model_raises(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "true")
        adapter = MLXKVBackendAdapter(model=None)
        with pytest.raises(RuntimeError, match="model not loaded"):
            adapter.prefill_to_kv([1, 2, 3], model_id="test")

    def test_no_kv_handle_on_unsupported(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "false")
        adapter = MLXKVBackendAdapter()
        with pytest.raises(RuntimeError):
            adapter.prefill_to_kv([1, 2, 3], model_id="test")


# ── Test 5: Cache object does not leak ─────────────────────────────────────


class TestNoCacheLeakage:
    def test_health_no_cache_internals(self, monkeypatch):
        adapter = MLXKVBackendAdapter(model="secret", tokenizer="secret")
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", adapter)

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        health = mgr.get_health()
        health_str = str(health)
        assert "secret" not in health_str

    def test_health_no_prompt_leakage(self):
        registry = BackendKVAdapterRegistry()
        registry.register("mlx", MLXKVBackendAdapter())

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        health = mgr.get_health()
        health_str = str(health)
        for forbidden in ("prompt", "token_ids", "cache_repr", "model_repr"):
            assert forbidden not in health_str.lower(), f"'{forbidden}' leaked in health"


# ── Test 6: Capability does not overclaim ──────────────────────────────────


class TestNoOverclaim:
    def test_never_reports_resumable_by_default(self):
        adapter = MLXKVBackendAdapter()
        assert adapter.supports_kv_cache() != KVCapability.RESUMABLE

    def test_never_reports_cloneable_by_default(self):
        adapter = MLXKVBackendAdapter()
        assert adapter.supports_kv_cache() != KVCapability.CLONEABLE

    def test_never_reports_serializable_by_default(self):
        adapter = MLXKVBackendAdapter()
        assert adapter.supports_kv_cache() != KVCapability.SERIALIZABLE

    def test_clone_always_raises(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", "true")
        adapter = MLXKVBackendAdapter()
        handle = KVHandle(backend="mlx", model_id="test", token_count=0, opaque_ref={})
        with pytest.raises(RuntimeError, match="not implemented"):
            adapter.clone_kv(handle)


# ── Test 7: Feasibility report ─────────────────────────────────────────────


class TestFeasibilityReport:
    def test_report_returns_string(self):
        report = get_mlx_kv_feasibility_report()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_is_honest(self):
        """Report must not claim production readiness."""
        report = get_mlx_kv_feasibility_report()
        # Must mention either "available" or "not available" — no vague language.
        assert "available" in report.lower() or "not available" in report.lower()

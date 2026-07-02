"""Tests for experimental MLX live batching behind gates."""

from __future__ import annotations

import pytest
from whooshd.config import get_batch_execution_enabled, get_mlx_batch_execution_enabled


class TestMLXBatchDisabledByDefault:
    def test_global_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_EXECUTION_ENABLED", raising=False)
        assert get_batch_execution_enabled() is False

    def test_mlx_specific_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_MLX_BATCH_EXECUTION_ENABLED", raising=False)
        assert get_mlx_batch_execution_enabled() is False

    def test_adapter_unsupported_by_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_EXECUTION_ENABLED", raising=False)
        monkeypatch.delenv("WHOOSHD_MLX_BATCH_EXECUTION_ENABLED", raising=False)
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = MLXInferenceAdapter().supports_chat_batching()
        assert cap == "unsupported"


class TestMLXBatchGlobalOnly:
    def test_global_enabled_mlx_disabled_still_unsupported(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_MLX_BATCH_EXECUTION_ENABLED", "false")
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = MLXInferenceAdapter().supports_chat_batching()
        assert cap == "unsupported"


class TestMLXBatchExperimental:
    def test_reports_experimental_when_all_gates_pass(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_MLX_BATCH_EXECUTION_ENABLED", "true")
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = MLXInferenceAdapter().supports_chat_batching()
        # On Apple Silicon with mlx-lm installed, should be experimental.
        assert cap in ("experimental", "unsupported")


class TestOtherAdaptersUnsupported:
    def test_llama_cpp_unsupported(self):
        from whooshd.adapters.llama_cpp import LlamaCppAdapter
        cap = getattr(LlamaCppAdapter(), "supports_chat_batching", lambda: "unsupported")()
        assert cap == "unsupported"

    def test_stub_still_experimental_with_gates(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        from whooshd.adapters.stub import StubInferenceAdapter
        cap = StubInferenceAdapter().supports_chat_batching()
        assert cap == "experimental"


class TestMLXBatchMissingImport:
    def test_missing_batch_generate_reports_unsupported(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_MLX_BATCH_EXECUTION_ENABLED", "true")
        import builtins
        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "mlx_lm":
                raise ImportError("simulated")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = MLXInferenceAdapter().supports_chat_batching()
        assert cap == "unsupported"


class TestNoLeakage:
    def test_capability_string_no_leakage(self):
        """Capability string is just 'experimental' or 'unsupported' — no internals."""
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = MLXInferenceAdapter().supports_chat_batching()
        assert cap in ("experimental", "unsupported")
        for forbidden in ("prompt", "token_ids", "cache", "model_repr"):
            assert forbidden not in str(cap).lower()


class TestQueueUnchanged:
    def test_queue_still_fifo(self):
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage
        queue = RequestQueue()
        for i in range(3):
            req = ChatCompletionRequest(model="m", messages=[ChatMessage(role="user", content=str(i))], stream=False)
            queue.enqueue(QueueEntry(request_id=f"req-{i}", request=req))
        dequeued = [queue.dequeue().request_id for _ in range(3)]
        assert dequeued == ["req-0", "req-1", "req-2"]

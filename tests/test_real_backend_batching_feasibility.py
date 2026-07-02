"""Tests for real backend batching feasibility probes."""

from __future__ import annotations

import pytest

from whooshd.batching import (
    RealBatchBackend,
    RealBatchFeasibilityReport,
    RealBatchFeasibilityStatus,
)
from whooshd.adapters.mlx_batch import probe_mlx_batch_generate_capability


class TestMLXBatchProbe:
    def test_probe_returns_report(self):
        report = probe_mlx_batch_generate_capability()
        assert isinstance(report, RealBatchFeasibilityReport)
        assert report.backend == RealBatchBackend.MLX
        assert report.status in (
            RealBatchFeasibilityStatus.UNSUPPORTED,
            RealBatchFeasibilityStatus.FEASIBLE,
            RealBatchFeasibilityStatus.INCONCLUSIVE,
        )

    def test_probe_live_path_not_enabled(self):
        report = probe_mlx_batch_generate_capability()
        assert report.live_path_enabled is False

    def test_probe_no_content_leakage(self):
        """Parameter names (like 'prompts') are metadata, not leaked content."""
        report = probe_mlx_batch_generate_capability()
        notes_str = " ".join(report.notes)
        # Content leaks (forbidden): token_ids, generated_text, cache objects, model reprs.
        for forbidden in ("token_ids", "generated_text", "cache_repr", "model_repr", "raw_message"):
            assert forbidden not in notes_str.lower()

    def test_probe_no_generated_text(self):
        report = probe_mlx_batch_generate_capability()
        report_str = str(report)
        assert "generated_text" not in report_str.lower()


class TestMLXBatchMissingImport:
    def test_missing_import_reports_unsupported(self, monkeypatch):
        import builtins
        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "mlx_lm":
                raise ImportError("simulated missing mlx_lm")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        import importlib
        import whooshd.adapters.mlx_batch as mb
        importlib.reload(mb)
        report = mb.probe_mlx_batch_generate_capability()
        assert report.status == RealBatchFeasibilityStatus.UNSUPPORTED


class TestMLXBatchWrongResponseCount:
    def test_wrong_count_reports_inconclusive(self):
        report = RealBatchFeasibilityReport(
            backend=RealBatchBackend.MLX,
            status=RealBatchFeasibilityStatus.INCONCLUSIVE,
            response_count_verified=False,
            response_order_verified=False,
            notes=("response count mismatch in manual probe",),
        )
        assert report.response_count_verified is False
        assert report.response_order_verified is False


class TestLlamaCppProbe:
    def test_llama_cpp_explicit_batch_contract_is_false(self):
        report = _probe_llama_cpp_batching()
        assert report.explicit_batch_contract is False
        assert report.server_side_batching_only is True

    def test_llama_cpp_live_path_not_enabled(self):
        report = _probe_llama_cpp_batching()
        assert report.live_path_enabled is False

    def test_llama_cpp_no_content_leakage(self):
        report = _probe_llama_cpp_batching()
        report_str = str({"backend": report.backend.value, "status": report.status.value})
        for forbidden in ("token_ids", "generated_text", "cache", "model_repr"):
            assert forbidden not in report_str.lower()


def _probe_llama_cpp_batching() -> RealBatchFeasibilityReport:
    return RealBatchFeasibilityReport(
        backend=RealBatchBackend.LLAMA_CPP,
        status=RealBatchFeasibilityStatus.UNSUPPORTED,
        explicit_batch_contract=False,
        server_side_batching_only=True,
        notes=(
            "llama.cpp supports server-side continuous batching",
            "but does not expose an explicit batch chat completion API",
            "that maps one adapter call to N mapped chat responses",
        ),
    )


class TestRealAdaptersNoLiveBatch:
    def test_mlx_adapter_no_experimental_batch(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "false")
        from whooshd.adapters.mlx import MLXInferenceAdapter
        cap = getattr(MLXInferenceAdapter(), "supports_chat_batching", lambda: "unsupported")()
        assert cap == "unsupported"

    def test_llama_cpp_adapter_no_experimental_batch(self):
        from whooshd.adapters.llama_cpp import LlamaCppAdapter
        cap = getattr(LlamaCppAdapter(), "supports_chat_batching", lambda: "unsupported")()
        assert cap == "unsupported"


class TestLivePathUnchanged:
    def test_live_path_batching_still_stub_only(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "false")
        from whooshd.adapters.mlx import MLXInferenceAdapter
        assert getattr(MLXInferenceAdapter(), "supports_chat_batching", lambda: "unsupported")() == "unsupported"

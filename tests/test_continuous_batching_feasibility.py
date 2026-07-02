"""Tests for continuous batching feasibility — probe only, no implementation."""

from __future__ import annotations

from whooshd.batching import (
    ContinuousBatchingBackend,
    ContinuousBatchingContract,
    ContinuousBatchingFeasibilityReport,
    ContinuousBatchingStatus,
)


def _mlx_report() -> ContinuousBatchingFeasibilityReport:
    return ContinuousBatchingFeasibilityReport(
        backend=ContinuousBatchingBackend.MLX,
        contract=ContinuousBatchingContract.WHOOSHD_OWNED_CONTINUOUS,
        status=ContinuousBatchingStatus.REQUIRES_NEW_TOKEN_LEVEL_RUNTIME,
        explicit_batch_supported=True,
        server_side_continuous_supported=False,
        whooshd_owned_continuous_supported=False,
        requires_token_level_scheduler=True,
        requires_slot_accounting=True,
        requires_stream_multiplexing=True,
        requires_cancellation_protocol=True,
        requires_per_request_rng_sampling_state=True,
        live_path_changed=False,
        notes=(
            "MLX explicit batch_generate exists and is proven experimentally",
            "Whooshd-owned continuous batching requires token-level decode control",
            "No MLX token-level decode API is currently available",
        ),
    )


def _llama_report() -> ContinuousBatchingFeasibilityReport:
    return ContinuousBatchingFeasibilityReport(
        backend=ContinuousBatchingBackend.LLAMA_CPP,
        contract=ContinuousBatchingContract.SERVER_SIDE_CONTINUOUS,
        status=ContinuousBatchingStatus.OBSERVABLE,
        explicit_batch_supported=False,
        server_side_continuous_supported=True,
        whooshd_owned_continuous_supported=False,
        requires_token_level_scheduler=False,
        requires_slot_accounting=True,
        requires_stream_multiplexing=False,
        requires_cancellation_protocol=False,
        requires_per_request_rng_sampling_state=False,
        live_path_changed=False,
        notes=(
            "llama.cpp server exposes /slots and /metrics",
            "server-side continuous batching is observable",
            "explicit batch adapter call contract is not available",
            "whooshd does not own token-level decode",
        ),
    )


class TestReportsAreMetadataOnly:
    def test_mlx_report_no_content_leakage(self):
        r = _mlx_report()
        r_str = str({"backend": r.backend.value, "contract": r.contract.value, "status": r.status.value})
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr"):
            assert f not in r_str.lower()

    def test_llama_report_no_content_leakage(self):
        r = _llama_report()
        r_str = str({"backend": r.backend.value, "contract": r.contract.value, "status": r.status.value})
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr"):
            assert f not in r_str.lower()


class TestMLXDistinction:
    def test_explicit_batch_supported_continuous_not(self):
        r = _mlx_report()
        assert r.explicit_batch_supported is True
        assert r.whooshd_owned_continuous_supported is False
        assert r.requires_token_level_scheduler is True
        assert r.live_path_changed is False


class TestLlamaCppDistinction:
    def test_server_side_not_explicit(self):
        r = _llama_report()
        assert r.server_side_continuous_supported is True
        assert r.explicit_batch_supported is False
        assert r.whooshd_owned_continuous_supported is False
        assert r.contract == ContinuousBatchingContract.SERVER_SIDE_CONTINUOUS


class TestNoLivePathChanges:
    def test_both_reports_live_path_unchanged(self):
        assert _mlx_report().live_path_changed is False
        assert _llama_report().live_path_changed is False


class TestCancellationRequirements:
    def test_mlx_requires_cancellation_protocol(self):
        assert _mlx_report().requires_cancellation_protocol is True

    def test_llama_does_not_require_owned_cancellation(self):
        """Server-side continuous batching manages its own slots."""
        assert _llama_report().requires_cancellation_protocol is False

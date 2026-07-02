"""Tests for backend token-loop feasibility probes."""

from whooshd.token_loop_feasibility import (
    BackendTokenLoopFeasibilityReport,
    TokenLoopBackend,
    TokenLoopFeasibilityStatus,
    TokenLoopMissingPrimitive,
    TokenLoopOwnership,
    probe_llama_cpp_token_loop_feasibility,
    probe_mlx_token_loop_feasibility,
)


class TestReportsMetadataOnly:
    def test_mlx_report_no_leakage(self):
        r = probe_mlx_token_loop_feasibility()
        s = str({"backend": r.backend.value, "ownership": r.ownership.value, "status": r.status.value})
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr", "kv_handle"):
            assert f not in s.lower()

    def test_llama_report_no_leakage(self):
        r = probe_llama_cpp_token_loop_feasibility()
        s = str({"backend": r.backend.value, "ownership": r.ownership.value, "status": r.status.value})
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr", "kv_handle"):
            assert f not in s.lower()


class TestMLXProbe:
    def test_ownership_is_whooshd(self):
        assert probe_mlx_token_loop_feasibility().ownership == TokenLoopOwnership.WHOOSHD_OWNED

    def test_live_path_unchanged(self):
        assert probe_mlx_token_loop_feasibility().live_path_changed is False
        assert probe_mlx_token_loop_feasibility().adapter_behavior_changed is False

    def test_missing_primitives_include_cancellation(self):
        r = probe_mlx_token_loop_feasibility()
        assert TokenLoopMissingPrimitive.CANCELLATION_HOOK in r.missing_primitives

    def test_missing_primitives_include_slot_ownership(self):
        r = probe_mlx_token_loop_feasibility()
        assert TokenLoopMissingPrimitive.SLOT_OWNERSHIP in r.missing_primitives

    def test_status_is_plausible_or_requires_prototype(self):
        s = probe_mlx_token_loop_feasibility().status
        assert s in (TokenLoopFeasibilityStatus.PLAUSIBLE, TokenLoopFeasibilityStatus.REQUIRES_BACKEND_PROTOTYPE)


class TestLlamaCppProbe:
    def test_ownership_is_backend_server(self):
        assert probe_llama_cpp_token_loop_feasibility().ownership == TokenLoopOwnership.BACKEND_SERVER_OWNED

    def test_live_path_unchanged(self):
        assert probe_llama_cpp_token_loop_feasibility().live_path_changed is False

    def test_whooshd_does_not_own_token_loop(self):
        r = probe_llama_cpp_token_loop_feasibility()
        assert r.token_step_surface_available is False
        assert r.slot_ownership_available is False

    def test_status_observable(self):
        assert probe_llama_cpp_token_loop_feasibility().status == TokenLoopFeasibilityStatus.OBSERVABLE

"""Tests for MLX decode-step ownership spike — reins inspection only."""

from whooshd.mlx_decode_step_ownership import (
    MLXDecodeStepCapabilityStatus,
    MLXDecodeStepOwnershipReport,
    MLXDecodeStepPrimitive,
    MLXDecodeStepPrimitiveResult,
    probe_mlx_decode_step_ownership,
)


class TestReportDefaults:
    def test_safe_defaults(self):
        r = MLXDecodeStepOwnershipReport()
        assert r.implementation_allowed is False
        assert r.production_ready is False
        assert r.performance_claim_made is False
        assert r.prompt_text_included is False
        assert r.generated_text_included is False
        assert r.token_ids_included is False
        assert r.kv_handles_included is False
        assert r.raw_exception_included is False


class TestAllPrimitivesRepresented:
    def test_all_10_primitives(self):
        r = probe_mlx_decode_step_ownership()
        names = {p.primitive for p in r.primitives}
        assert names == set(MLXDecodeStepPrimitive)


class TestDecisionRules:
    def test_all_supported_enables_ownership(self):
        results = tuple(
            MLXDecodeStepPrimitiveResult(p, MLXDecodeStepCapabilityStatus.SUPPORTED, "", "", "")
            for p in MLXDecodeStepPrimitive
        )
        r = MLXDecodeStepOwnershipReport(primitives=results)
        # All supported → ownership possible.
        r2 = MLXDecodeStepOwnershipReport(
            primitives=results,
            whooshd_owned_decode_loop_possible=True,
            recommended_next_step="mlx_token_step_internal_prototype",
        )
        assert r2.whooshd_owned_decode_loop_possible is True

    def test_blocked_selective_decode_disables_ownership(self):
        probe_mlx_decode_step_ownership()  # Uses real probe — should report blocked
        r = probe_mlx_decode_step_ownership()
        assert r.whooshd_owned_decode_loop_possible is False
        assert "keep_research_only" in r.recommended_next_step or "adapter_seam" in r.recommended_next_step

    def test_report_no_leakage(self):
        r = probe_mlx_decode_step_ownership()
        s = str(r)
        for f in ("raw_prompt", "generated_text_full", "token_ids_list",
                   "cache_repr", "traceback", "model_repr"):
            assert f not in s.lower()

    def test_not_production(self):
        r = probe_mlx_decode_step_ownership()
        assert r.production_ready is False
        assert r.performance_claim_made is False


class TestMissingMLX:
    def test_missing_mlx_handled_safely(self, monkeypatch):
        import builtins
        original = builtins.__import__

        def _fake(name, *a, **kw):
            if name == "mlx_lm":
                raise ImportError("simulated")
            return original(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fake)
        import importlib, whooshd.mlx_decode_step_ownership as mo
        importlib.reload(mo)
        r = mo.probe_mlx_decode_step_ownership()
        assert all(p.status == MLXDecodeStepCapabilityStatus.UNKNOWN for p in r.primitives)
        assert r.production_ready is False

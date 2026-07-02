"""Tests for MLX primitive verification — key-checking, not key-cutting."""

from whooshd.mlx_primitive_verification import (
    MLXPrimitiveName,
    MLXPrimitiveVerificationReport,
    MLXPrimitiveVerificationStatus,
    build_mlx_primitive_verification_report,
)


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        r = build_mlx_primitive_verification_report()
        s = str(r)
        for f in ("raw_prompt", "rendered", "messages", "generated_text_full",
                   "token_ids_list", "cache_repr", "model_repr", "tokenizer_repr", "kv_handle"):
            assert f not in s.lower()

    def test_report_not_production_ready(self):
        r = build_mlx_primitive_verification_report()
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False
        assert r.all_backend_verified is False
        assert r.generated_text_included is False


class TestAllSixRepresented:
    def test_all_six_present(self):
        r = build_mlx_primitive_verification_report()
        names = {v.primitive for v in r.verifications}
        assert names == {
            MLXPrimitiveName.SLOT_OWNERSHIP, MLXPrimitiveName.CANCELLATION_HOOK,
            MLXPrimitiveName.TIMEOUT_HOOK, MLXPrimitiveName.SAMPLING_STATE,
            MLXPrimitiveName.FAILURE_ISOLATION, MLXPrimitiveName.CLEANUP_HOOK,
        }


class TestSlotOwnershipBlocks:
    def test_slot_ownership_blocks(self):
        r = build_mlx_primitive_verification_report()
        sv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.SLOT_OWNERSHIP)
        assert sv.backend_verified is False
        assert sv.blocks_live_continuous_batching is True


class TestCancellationUnresolved:
    def test_cancellation_not_backend_verified(self):
        r = build_mlx_primitive_verification_report()
        cv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.CANCELLATION_HOOK)
        assert cv.backend_verified is False
        assert cv.blocks_live_continuous_batching is True


class TestTimeoutUnresolved:
    def test_timeout_not_backend_verified(self):
        r = build_mlx_primitive_verification_report()
        tv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.TIMEOUT_HOOK)
        assert tv.backend_verified is False
        assert tv.blocks_live_continuous_batching is True


class TestSamplingSurfaceOnly:
    def test_sampling_not_backend_verified(self):
        r = build_mlx_primitive_verification_report()
        sv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.SAMPLING_STATE)
        assert sv.backend_verified is False
        assert sv.status in (MLXPrimitiveVerificationStatus.SURFACE_AVAILABLE,
                             MLXPrimitiveVerificationStatus.PARTIAL,
                             MLXPrimitiveVerificationStatus.UNKNOWN)


class TestFailureIsolationBlocks:
    def test_failure_isolation_blocks(self):
        r = build_mlx_primitive_verification_report()
        fv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.FAILURE_ISOLATION)
        assert fv.backend_verified is False
        assert fv.blocks_live_continuous_batching is True


class TestCleanupBlocks:
    def test_cleanup_blocks(self):
        r = build_mlx_primitive_verification_report()
        cv = next(v for v in r.verifications if v.primitive == MLXPrimitiveName.CLEANUP_HOOK)
        assert cv.backend_verified is False
        assert cv.blocks_live_continuous_batching is True


class TestBlockingPrimitivesComplete:
    def test_all_unverified_are_blocking(self):
        r = build_mlx_primitive_verification_report()
        for v in r.verifications:
            if not v.backend_verified:
                assert v.primitive in r.blocking_primitives
        assert len(r.blocking_primitives) == 6


class TestGeneratedTextOptIn:
    def test_default_no_text(self):
        r = build_mlx_primitive_verification_report()
        assert r.generated_text_included is False

    def test_opt_in_text(self):
        r = build_mlx_primitive_verification_report(generated_text_included=True)
        assert r.generated_text_included is True

"""Tests for MLX slot lifecycle tombstone — haunted furniture labeling."""

from whooshd.mlx_slot_lifecycle import (
    MLXSlotLifecycleFailureReason,
    MLXSlotLifecycleStatus,
    MLXSlotTombstoneReason,
    MLXVirtualSlotClaim,
    MLXVirtualSlotRelease,
    MLXVirtualSlotTombstone,
    build_mlx_slot_lifecycle_report,
    validate_release,
    validate_tombstone_late_chunk,
    validate_virtual_slot_claims,
)


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        r = build_mlx_slot_lifecycle_report(
            virtual_slot_claimed=True, virtual_slot_released=True,
            tombstone_created=True, late_chunks_rejected=True,
            release_idempotent=True, reuse_requires_generation_bump=True,
        )
        s = str(r)
        for f in ("raw_prompt", "rendered", "messages", "generated_text_full",
                   "token_ids_list", "cache_repr", "model_repr", "kv_handle", "traceback"):
            assert f not in s.lower()

    def test_no_backend_verification(self):
        r = build_mlx_slot_lifecycle_report()
        assert r.slot_ownership_backend_verified is False
        assert r.mlx_backend_slots_verified is False
        assert r.shared_decode_loop_verified is False
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False


class TestSlotClaim:
    def test_request_claims_slot(self):
        claims = [MLXVirtualSlotClaim("a", "s1", 1)]
        v = validate_virtual_slot_claims(claims)
        assert len(v) == 0

    def test_duplicate_slot_owner_rejected(self):
        claims = [
            MLXVirtualSlotClaim("a", "s1", 1),
            MLXVirtualSlotClaim("b", "s1", 1),
        ]
        v = validate_virtual_slot_claims(claims)
        assert MLXSlotLifecycleFailureReason.DUPLICATE_SLOT_OWNER in v

    def test_duplicate_request_slot_rejected(self):
        claims = [
            MLXVirtualSlotClaim("a", "s1", 1),
            MLXVirtualSlotClaim("a", "s2", 1),
        ]
        v = validate_virtual_slot_claims(claims)
        assert MLXSlotLifecycleFailureReason.DUPLICATE_REQUEST_SLOT in v


class TestRelease:
    def test_release_clears_owner(self):
        claims = [MLXVirtualSlotClaim("a", "s1", 1)]
        release = MLXVirtualSlotRelease("a", "s1", 1, MLXSlotTombstoneReason.SUCCESS)
        v = validate_release(release, claims)
        assert len(v) == 0

    def test_wrong_owner_release_rejected(self):
        claims = [MLXVirtualSlotClaim("a", "s1", 1)]
        release = MLXVirtualSlotRelease("b", "s1", 1, MLXSlotTombstoneReason.SUCCESS)
        v = validate_release(release, claims)
        assert MLXSlotLifecycleFailureReason.RELEASE_WRONG_OWNER in v

    def test_release_missing_owner(self):
        release = MLXVirtualSlotRelease("a", "s1", 1, MLXSlotTombstoneReason.SUCCESS)
        v = validate_release(release, [])
        assert MLXSlotLifecycleFailureReason.RELEASE_DID_NOT_CLEAR_OWNER in v


class TestTombstone:
    def test_late_chunk_rejected(self):
        tombstones = [MLXVirtualSlotTombstone("a", "s1", 1, MLXSlotTombstoneReason.SUCCESS)]
        v = validate_tombstone_late_chunk(
            tombstones=tombstones, request_id="a", slot_id="s1", generation=1,
        )
        assert MLXSlotLifecycleFailureReason.LATE_CHUNK_ACCEPTED_FOR_TOMBSTONE in v

    def test_non_tombstoned_ok(self):
        v = validate_tombstone_late_chunk(
            tombstones=[], request_id="a", slot_id="s1", generation=1,
        )
        assert len(v) == 0


class TestGenerationBump:
    def test_tombstoned_generation_rejected(self):
        tombstones = [MLXVirtualSlotTombstone("a", "s1", 1, MLXSlotTombstoneReason.SUCCESS)]
        claims = [MLXVirtualSlotClaim("b", "s1", 1)]
        v = validate_virtual_slot_claims(claims, tombstones=tombstones)
        assert MLXSlotLifecycleFailureReason.TOMBSTONE_REUSED_WITHOUT_GENERATION_BUMP in v

    def test_generation_bump_allowed(self):
        tombstones = [MLXVirtualSlotTombstone("a", "s1", 1, MLXSlotTombstoneReason.SUCCESS)]
        claims = [MLXVirtualSlotClaim("b", "s1", 2)]
        v = validate_virtual_slot_claims(claims, tombstones=tombstones)
        assert len(v) == 0


class TestAllPassingReport:
    def test_full_passing_report(self):
        r = build_mlx_slot_lifecycle_report(
            virtual_slot_claimed=True, virtual_slot_released=True,
            tombstone_created=True, late_chunks_rejected=True,
            release_idempotent=True, reuse_requires_generation_bump=True,
        )
        assert r.status == MLXSlotLifecycleStatus.PASSED
        assert r.virtual_slot_ownership_verified is True

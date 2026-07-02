"""Tests for fake token-level scheduler prototype — toy control tower."""

import time
import pytest
from whooshd.continuous_batching import (
    ContinuousBatchingStatus,
    ContinuousBatchInvariantViolation,
    ContinuousFinishReason,
    ContinuousRequestHandle,
    ContinuousRequestState,
    ContinuousSlotState,
)
from whooshd.continuous_scheduler import (
    FakeContinuousSchedulerConfig,
    FakeTokenLevelScheduler,
)


def _handle(rid="req-a", model="m", backend="mlx", stream=False):
    return ContinuousRequestHandle(request_id=rid, model=model, backend=backend, stream=stream, admitted_at=time.time())


class TestSchedulerStartsContractOnly:
    def test_status_is_contract_only(self):
        sched = FakeTokenLevelScheduler()
        snap = sched.snapshot()
        assert snap.status == ContinuousBatchingStatus.CONTRACT_ONLY


class TestAdmission:
    def test_admits_into_slots(self):
        sched = FakeTokenLevelScheduler(FakeContinuousSchedulerConfig(max_slots=2))
        sched.admit(_handle("a"))
        sched.admit(_handle("b"))
        sched.tick()
        snap = sched.snapshot()
        assert snap.admitted_count == 2
        assert snap.active_slot_count >= 1

    def test_third_request_waits(self):
        sched = FakeTokenLevelScheduler(FakeContinuousSchedulerConfig(max_slots=2))
        for rid in ("a", "b", "c"):
            sched.admit(_handle(rid))
        sched.tick()
        snap = sched.snapshot()
        assert snap.active_slot_count <= 2
        violations = sched.validate()
        assert ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT not in violations


class TestDecodeTicks:
    def test_produces_per_request_chunks(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.admit(_handle("b"))
        for _ in range(5):
            sched.tick()

        chunks_a = sched.drain_outputs("a")
        chunks_b = sched.drain_outputs("b")
        assert len(chunks_a) > 0
        assert len(chunks_b) > 0
        assert all(c.request_id == "a" for c in chunks_a)
        assert all(c.request_id == "b" for c in chunks_b)

    def test_chunks_have_monotonic_sequence(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        for _ in range(5):
            sched.tick()
        chunks = sched.drain_outputs("a")
        seqs = [c.sequence_index for c in chunks]
        assert seqs == sorted(set(seqs))


class TestCancellation:
    def test_cancel_before_prefill_no_chunks(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.cancel("a")
        sched.tick()
        chunks = sched.drain_outputs("a")
        assert len(chunks) == 0
        violations = sched.validate()
        assert len(violations) == 0

    def test_cancel_during_decode_stops_future_chunks(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.admit(_handle("b"))
        for _ in range(3):
            sched.tick()
        sched.cancel("a")
        before = len(sched.drain_outputs("a"))
        for _ in range(2):
            sched.tick()
        after = len(sched.drain_outputs("a"))
        assert after == 0  # No new chunks after cancel
        chunks_b = sched.drain_outputs("b")
        assert len(chunks_b) > before  # Peer continues
        violations = sched.validate()
        assert len(violations) == 0


class TestTimeout:
    def test_timeout_during_decode_isolates_peer(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.admit(_handle("b"))
        for _ in range(3):
            sched.tick()
        sched.drain_outputs("a")  # Clear pre-timeout chunks
        sched.timeout("a")
        for _ in range(2):
            sched.tick()
        chunks_a = sched.drain_outputs("a")
        assert len(chunks_a) == 0  # No new chunks after timeout
        chunks_b = sched.drain_outputs("b")
        assert len(chunks_b) > 0
        violations = sched.validate()
        assert len(violations) == 0


class TestFailureIsolation:
    def test_per_request_failure_isolates_peer(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.admit(_handle("b"))
        for _ in range(2):
            sched.tick()
        sched.drain_outputs("a")  # Clear pre-failure chunks
        sched.fail_request("a")
        for _ in range(2):
            sched.tick()
        chunks_a = sched.drain_outputs("a")
        assert len(chunks_a) == 0
        chunks_b = sched.drain_outputs("b")
        assert len(chunks_b) > 0
        violations = sched.validate()
        assert len(violations) == 0


class TestSnapshotPrivacy:
    def test_snapshot_no_prompt_leakage(self):
        sched = FakeTokenLevelScheduler()
        sched.admit(_handle("a"))
        sched.tick()
        snap = sched.snapshot()
        snap_str = str(snap)
        for f in ("prompt", "token_ids", "generated_text", "cache", "model_repr", "kv_handle"):
            assert f not in snap_str.lower()


class TestNoDuplicateSlots:
    def test_validation_catches_duplicate(self):
        from whooshd.continuous_batching import (
            ContinuousSlot, ContinuousSlotState,
            validate_slot_assignments,
            ContinuousBatchInvariantViolation,
        )
        slots = [
            ContinuousSlot("s1", ContinuousSlotState.DECODING, "a"),
            ContinuousSlot("s2", ContinuousSlotState.DECODING, "a"),
        ]
        violations = validate_slot_assignments(slots)
        assert ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT in violations

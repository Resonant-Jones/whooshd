"""Tests for continuous batching runtime contract — invariants only."""

from whooshd.continuous_batching import (
    ContinuousBatchingStatus,
    ContinuousBatchInvariantViolation,
    ContinuousDecodeStep,
    ContinuousFinishReason,
    ContinuousOutputChunk,
    ContinuousRequestHandle,
    ContinuousRequestState,
    ContinuousRuntimeSnapshot,
    ContinuousSlot,
    ContinuousSlotState,
    validate_output_demux,
    validate_slot_assignments,
    validate_terminal_state_not_reentered,
)


class TestStatus:
    def test_default_is_contract_only(self):
        snapshot = ContinuousRuntimeSnapshot()
        assert snapshot.status == ContinuousBatchingStatus.CONTRACT_ONLY
        assert snapshot.active_request_count == 0


class TestSlotInvariants:
    def test_duplicate_request_detected(self):
        slots = [
            ContinuousSlot("s1", ContinuousSlotState.DECODING, "req-a"),
            ContinuousSlot("s2", ContinuousSlotState.DECODING, "req-a"),
        ]
        violations = validate_slot_assignments(slots)
        assert ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT in violations

    def test_released_slot_no_request(self):
        slots = [ContinuousSlot("s1", ContinuousSlotState.RELEASED, "req-a")]
        violations = validate_slot_assignments(slots)
        assert ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT in violations

    def test_valid_slots_no_violations(self):
        slots = [
            ContinuousSlot("s1", ContinuousSlotState.DECODING, "req-a"),
            ContinuousSlot("s2", ContinuousSlotState.DECODING, "req-b"),
        ]
        violations = validate_slot_assignments(slots)
        assert len(violations) == 0


class TestOutputDemux:
    def test_unknown_request_rejected(self):
        chunks = [ContinuousOutputChunk("bad", "s1", 0)]
        violations = validate_output_demux(chunks, {"req-a"}, {"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in violations

    def test_unknown_slot_rejected(self):
        chunks = [ContinuousOutputChunk("req-a", "bad", 0)]
        violations = validate_output_demux(chunks, {"req-a"}, {"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in violations

    def test_out_of_order_sequence_rejected(self):
        chunks = [
            ContinuousOutputChunk("req-a", "s1", 2),
            ContinuousOutputChunk("req-a", "s1", 1),
        ]
        violations = validate_output_demux(chunks, {"req-a"}, {"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in violations

    def test_valid_demux_no_violations(self):
        chunks = [
            ContinuousOutputChunk("req-a", "s1", 0),
            ContinuousOutputChunk("req-b", "s2", 0),
            ContinuousOutputChunk("req-a", "s1", 1, finish_reason=ContinuousFinishReason.STOP),
        ]
        violations = validate_output_demux(chunks, {"req-a", "req-b"}, {"s1", "s2"})
        assert len(violations) == 0


class TestTerminalState:
    def test_completed_cannot_reenter(self):
        handles = [ContinuousRequestHandle("req-a", "m", "mlx", False, 1.0)]
        violations = validate_terminal_state_not_reentered(
            handles, {"req-a": ContinuousRequestState.COMPLETED},
        )
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in violations

    def test_active_can_enter(self):
        handles = [ContinuousRequestHandle("req-a", "m", "mlx", False, 1.0)]
        violations = validate_terminal_state_not_reentered(
            handles, {"req-a": ContinuousRequestState.ADMITTED},
        )
        assert len(violations) == 0


class TestSnapshotPrivacy:
    def test_snapshot_is_metadata_only(self):
        snapshot = ContinuousRuntimeSnapshot(
            decoding_count=2, completed_count=3, failed_count=1,
        )
        snapshot_str = str(snapshot)
        for forbidden in ("prompt", "token_ids", "generated_text", "cache", "model_repr", "kv_handle"):
            assert forbidden not in snapshot_str.lower()

"""Tests for fake streaming demux — stream goblin containment chamber."""

from whooshd.continuous_batching import (
    ContinuousBatchInvariantViolation,
    ContinuousOutputChunk,
)
from whooshd.continuous_streaming import FakeStreamingDemux


class TestStreamManagement:
    def test_opens_independent_streams(self):
        d = FakeStreamingDemux()
        d.open_stream("r1"); d.open_stream("r2")
        assert d.snapshot().open_stream_count == 2

    def test_open_is_idempotent(self):
        d = FakeStreamingDemux()
        d.open_stream("r1"); d.open_stream("r1")
        assert d.snapshot().open_stream_count == 1


class TestChunkRouting:
    def test_routes_to_matching_request(self):
        d = FakeStreamingDemux()
        d.open_stream("r1"); d.open_stream("r2")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 0, text="x"), active_request_ids={"r1", "r2"}, active_slot_ids={"s1", "s2"})
        assert len(v) == 0
        assert len(d.drain_events("r1")) == 1
        assert len(d.drain_events("r2")) == 0

    def test_sequence_order_preserved(self):
        d = FakeStreamingDemux(); d.open_stream("r1")
        assert not d.route_chunk(ContinuousOutputChunk("r1", "s1", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert not d.route_chunk(ContinuousOutputChunk("r1", "s1", 1), active_request_ids={"r1"}, active_slot_ids={"s1"})
        e = d.drain_events("r1")
        assert e[0].sequence_index == 0 and e[1].sequence_index == 1

    def test_out_of_order_rejected(self):
        d = FakeStreamingDemux(); d.open_stream("r1")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 1), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in v

    def test_unknown_request_rejected(self):
        d = FakeStreamingDemux()
        v = d.route_chunk(ContinuousOutputChunk("bad", "s1", 0), active_request_ids={"bad"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in v

    def test_unknown_slot_rejected(self):
        d = FakeStreamingDemux(); d.open_stream("r1")
        # "bad" is NOT in active_slot_ids — should be rejected.
        v = d.route_chunk(ContinuousOutputChunk("r1", "bad", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in v


class TestTerminalBehavior:
    def test_completed_rejects_chunks(self):
        d = FakeStreamingDemux(); d.open_stream("r1")
        d.route_chunk(ContinuousOutputChunk("r1", "s1", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        d.complete("r1")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 1), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in v

    def test_cancelled_rejects_chunks(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.cancel("r1")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in v

    def test_timed_out_rejects_chunks(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.timeout("r1")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in v

    def test_failed_rejects_chunks(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.fail("r1")
        v = d.route_chunk(ContinuousOutputChunk("r1", "s1", 0), active_request_ids={"r1"}, active_slot_ids={"s1"})
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in v


class TestPeerIsolation:
    def test_cancel_does_not_close_peer(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.open_stream("r2"); d.cancel("r1")
        v = d.route_chunk(ContinuousOutputChunk("r2", "s2", 0, text="x"), active_request_ids={"r2"}, active_slot_ids={"s2"})
        assert not v and len(d.drain_events("r2")) == 1

    def test_timeout_does_not_close_peer(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.open_stream("r2"); d.timeout("r1")
        v = d.route_chunk(ContinuousOutputChunk("r2", "s2", 0), active_request_ids={"r2"}, active_slot_ids={"s2"})
        assert not v

    def test_fail_does_not_close_peer(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.open_stream("r2"); d.fail("r1")
        v = d.route_chunk(ContinuousOutputChunk("r2", "s2", 0), active_request_ids={"r2"}, active_slot_ids={"s2"})
        assert not v


class TestSingleTerminalEvent:
    def test_complete_then_cancel_rejected(self):
        d = FakeStreamingDemux(); d.open_stream("r1"); d.complete("r1")
        assert ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED in d.cancel("r1")


class TestSnapshotPrivacy:
    def test_snapshot_no_leakage(self):
        d = FakeStreamingDemux(); d.open_stream("r1")
        d.route_chunk(ContinuousOutputChunk("r1", "s1", 0, text="x"), active_request_ids={"r1"}, active_slot_ids={"s1"})
        s = str(d.snapshot())
        for f in ("prompt", "token_ids", "cache", "model_repr", "kv_handle"):
            assert f not in s.lower()


class TestSchedulerIntegration:
    def test_scheduler_plus_demux(self):
        from whooshd.continuous_scheduler import FakeTokenLevelScheduler
        from whooshd.continuous_batching import ContinuousRequestHandle
        import time
        s = FakeTokenLevelScheduler()
        d = FakeStreamingDemux()
        s.admit(ContinuousRequestHandle("a", "m", "mlx", False, time.time()))
        s.admit(ContinuousRequestHandle("b", "m", "mlx", False, time.time()))
        d.open_stream("a"); d.open_stream("b")
        for _ in range(5):
            s.tick()
            for c in s.drain_outputs("a"):
                d.route_chunk(c, active_request_ids={"a", "b"}, active_slot_ids={"slot-0", "slot-1"})
            for c in s.drain_outputs("b"):
                d.route_chunk(c, active_request_ids={"a", "b"}, active_slot_ids={"slot-0", "slot-1"})
        ea = d.drain_events("a"); eb = d.drain_events("b")
        assert len(ea) > 0 and len(eb) > 0
        assert all(e.request_id == "a" for e in ea)
        assert all(e.request_id == "b" for e in eb)
        assert len(s.validate()) == 0

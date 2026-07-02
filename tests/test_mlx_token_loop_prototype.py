"""Tests for MLX token-loop prototype — fake-live boundary, goblin with tongs."""

from whooshd.mlx_token_loop import (
    MLXTokenLoopPrototypeReport,
    MLXTokenLoopPrototypeStatus,
    MISSING_PRIMITIVES,
    build_mlx_token_loop_report,
    normalize_mlx_stream_chunk,
)
from whooshd.continuous_batching import ContinuousFinishReason
from whooshd.continuous_streaming import FakeStreamingDemux


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        r = build_mlx_token_loop_report(chunks_observed=3, demux_routed_chunks=3)
        # Field names like 'token_ids_included' are metadata, not content leaks.
        # The report must not contain actual tokens, prompts, generated text, or internals.
        for f in ("raw_prompt", "rendered_prompt", "messages", "generated_text_full",
                   "cache_repr", "model_repr", "tokenizer_repr", "kv_handle"):
            assert f not in str(r).lower()

    def test_live_path_disabled(self):
        r = build_mlx_token_loop_report()
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False
        assert r.production_ready is False

    def test_missing_primitives_reported(self):
        r = build_mlx_token_loop_report()
        for p in ("slot_ownership", "cancellation_hook", "timeout_hook",
                   "sampling_state", "failure_isolation", "cleanup_hook"):
            assert p in r.missing_primitives


class TestChunkNormalization:
    def test_fake_chunk_normalizes(self):
        chunk = normalize_mlx_stream_chunk(
            request_id="r1", slot_id="s1", sequence_index=0,
            text="hello", include_text=True,
        )
        assert chunk.request_id == "r1"
        assert chunk.slot_id == "s1"
        assert chunk.sequence_index == 0
        assert chunk.text == "hello"
        assert chunk.finish_reason is None

    def test_default_no_generated_text(self):
        chunk = normalize_mlx_stream_chunk(
            request_id="r1", slot_id="s1", sequence_index=0,
            text="secret",
        )
        assert chunk.text is None  # include_text defaults to False

    def test_terminal_chunk(self):
        chunk = normalize_mlx_stream_chunk(
            request_id="r1", slot_id="s1", sequence_index=5,
            finish_reason=ContinuousFinishReason.STOP, include_text=True,
        )
        assert chunk.finish_reason == ContinuousFinishReason.STOP


class TestDemuxRouting:
    def test_chunks_route_through_fake_demux(self):
        demux = FakeStreamingDemux()
        demux.open_stream("r1")
        demux.open_stream("r2")

        for i in range(3):
            c = normalize_mlx_stream_chunk(request_id="r1", slot_id="s1", sequence_index=i, include_text=True)
            v = demux.route_chunk(c, active_request_ids={"r1", "r2"}, active_slot_ids={"s1", "s2"})
            assert not v

        events = demux.drain_events("r1")
        assert len(events) == 3
        assert events[0].sequence_index == 0
        assert events[2].sequence_index == 2

    def test_out_of_order_rejected(self):
        demux = FakeStreamingDemux()
        demux.open_stream("r1")
        demux.route_chunk(
            normalize_mlx_stream_chunk(request_id="r1", slot_id="s1", sequence_index=0),
            active_request_ids={"r1"}, active_slot_ids={"s1"},
        )
        from whooshd.continuous_batching import ContinuousBatchInvariantViolation
        v = demux.route_chunk(
            normalize_mlx_stream_chunk(request_id="r1", slot_id="s1", sequence_index=2),
            active_request_ids={"r1"}, active_slot_ids={"s1"},
        )
        assert ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH in v

    def test_terminal_event_observed(self):
        demux = FakeStreamingDemux()
        demux.open_stream("r1")
        demux.route_chunk(
            normalize_mlx_stream_chunk(request_id="r1", slot_id="s1", sequence_index=0, include_text=True),
            active_request_ids={"r1"}, active_slot_ids={"s1"},
        )
        demux.complete("r1")
        events = demux.drain_events("r1")
        assert events[-1].finish_reason == ContinuousFinishReason.STOP


class TestGeneratedTextOptIn:
    def test_default_report_no_text(self):
        r = build_mlx_token_loop_report()
        assert r.generated_text_included is False

    def test_explicit_text_inclusion(self):
        r = build_mlx_token_loop_report(generated_text_included=True)
        assert r.generated_text_included is True

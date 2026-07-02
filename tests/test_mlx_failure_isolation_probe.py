"""Tests for MLX failure isolation probe — blast-radius check."""

from whooshd.mlx_failure_isolation import (
    MLXFailureEvent,
    MLXFailureIsolationFailureReason,
    MLXFailureIsolationReport,
    MLXFailureIsolationStatus,
    MLXFailureScope,
    build_mlx_failure_event,
    build_mlx_failure_isolation_report,
    classify_mlx_failure_scope,
)


class TestReportMetadataOnly:
    def test_report_no_leakage(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=True,
        )
        s = str(r)
        for f in ("raw_prompt", "rendered", "messages", "generated_text_full",
                   "token_ids_list", "cache_repr", "model_repr", "kv_handle", "traceback"):
            assert f not in s.lower()


class TestScopeClassification:
    def test_per_request_scope(self):
        scope = classify_mlx_failure_scope(
            failed_request_id="a", active_request_ids=["a", "b"],
            affected_request_ids=["a"],
        )
        assert scope == MLXFailureScope.PER_REQUEST

    def test_backend_fatal(self):
        scope = classify_mlx_failure_scope(
            failed_request_id="a", active_request_ids=["a", "b"],
            affected_request_ids=["a"], backend_fatal=True,
        )
        assert scope == MLXFailureScope.BACKEND_FATAL

    def test_whole_batch(self):
        scope = classify_mlx_failure_scope(
            failed_request_id="a", active_request_ids=["a", "b"],
            affected_request_ids=["a", "b"],
        )
        assert scope == MLXFailureScope.WHOLE_BATCH


class TestTerminalState:
    def test_failed_request_terminal(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=True,
        )
        assert r.status == MLXFailureIsolationStatus.PASSED
        assert r.failed_request_terminal is True

    def test_failed_request_not_terminal(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=False, peers_continued=True, late_chunks_rejected=True,
        )
        assert r.status == MLXFailureIsolationStatus.FAILED
        assert r.failure_reason == MLXFailureIsolationFailureReason.FAILED_REQUEST_DID_NOT_TERMINATE


class TestPeerContinuation:
    def test_peers_continue(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=True,
        )
        assert r.peers_continued is True
        assert r.status == MLXFailureIsolationStatus.PASSED

    def test_peers_did_not_continue(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=False, late_chunks_rejected=True,
        )
        assert r.status == MLXFailureIsolationStatus.FAILED
        assert r.failure_reason == MLXFailureIsolationFailureReason.PEER_REQUEST_TERMINATED_UNEXPECTEDLY


class TestLateChunks:
    def test_late_chunks_rejected(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=True,
        )
        assert r.late_chunks_rejected is True
        assert r.status == MLXFailureIsolationStatus.PASSED

    def test_late_chunks_accepted(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=False,
        )
        assert r.status == MLXFailureIsolationStatus.FAILED
        assert r.failure_reason == MLXFailureIsolationFailureReason.LATE_CHUNK_ACCEPTED_AFTER_FAILURE


class TestSanitization:
    def test_exception_message_not_leaked(self):
        event = build_mlx_failure_event(request_id="a", error=RuntimeError("SECRET_PROMPT leaked"))
        assert "SECRET_PROMPT" not in event.sanitized_error_kind
        assert event.sanitized_error_kind == "RuntimeError"


class TestBackendUnverified:
    def test_all_backend_fields_false(self):
        event = build_mlx_failure_event(request_id="a")
        r = build_mlx_failure_isolation_report(
            failure_event=event, peer_request_ids=["b"],
            failed_request_terminal=True, peers_continued=True, late_chunks_rejected=True,
        )
        assert r.failure_isolation_backend_verified is False
        assert r.shared_decode_loop_verified is False
        assert r.production_ready is False
        assert r.live_path_enabled is False
        assert r.adapter_behavior_changed is False

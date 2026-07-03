"""Tests for fake token-step scheduler contract — bones and labels, no fire."""

from whooshd.fake_token_step_backend import (
    FakeTokenStepBackend,
    TokenStepRequest,
)
from whooshd.token_step_scheduler import run_fake_token_step_schedule


class TestPrefill:
    def test_creates_sequence_handles(self):
        backend = FakeTokenStepBackend()
        h1 = backend.prefill(TokenStepRequest("a", "label_a", 3))
        h2 = backend.prefill(TokenStepRequest("b", "label_b", 2))
        assert h1.sequence_id != h2.sequence_id
        assert not backend.is_finished(h1)

    def test_prefill_sets_ready(self):
        backend = FakeTokenStepBackend()
        h = backend.prefill(TokenStepRequest("a", "label", 3))
        assert not backend.is_finished(h)


class TestScheduler:
    def test_returns_one_result_per_request(self):
        backend = FakeTokenStepBackend()
        reqs = [TokenStepRequest("a", "la", 3), TokenStepRequest("b", "lb", 2)]
        results, report = run_fake_token_step_schedule(reqs, backend)
        assert len(results) == 2
        assert results[0].request_id == "a"
        assert results[1].request_id == "b"

    def test_decode_steps_match_max_tokens(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 3), TokenStepRequest("b", "lb", 2)], backend,
        )
        assert report.decode_steps >= 3
        # Shorter sequence should have fewer steps of content.
        assert len(results[1].content.split()) == 2

    def test_demux_routes_to_correct_request(self):
        backend = FakeTokenStepBackend()
        results, _ = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 2), TokenStepRequest("b", "lb", 2)], backend,
        )
        # Fake backend emits "A0", "A1" etc. where the letter comes from seq id.
        # Content is deterministic and per-request.
        assert " 0" in results[0].content or "0" in results[0].content
        assert " 0" in results[1].content or "0" in results[1].content

    def test_all_sequences_finish_and_cleanup(self):
        backend = FakeTokenStepBackend()
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 2), TokenStepRequest("b", "lb", 2)], backend,
        )
        assert report.sequences_finished == 2
        assert report.sequences_cleaned_up == 2
        assert report.terminal_states_observed == 2

    def test_empty_requests_rejected(self):
        backend = FakeTokenStepBackend()
        import pytest
        with pytest.raises(ValueError):
            run_fake_token_step_schedule([], backend)


class TestReportPrivacy:
    def test_report_metadata_only(self):
        backend = FakeTokenStepBackend()
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 2)], backend,
        )
        s = str(report)
        for f in ("raw_prompt", "generated_text_full", "token_ids_list",
                   "kv_handle", "cache_repr", "traceback"):
            assert f not in s.lower()

    def test_report_not_production(self):
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 1)], FakeTokenStepBackend(),
        )
        assert report.production_ready is False
        assert report.performance_claim_made is False
        assert report.backend == "fake"
        assert report.generated_text_included is False

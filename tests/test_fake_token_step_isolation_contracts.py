"""Tests for fake token-step isolation contracts — joints and reflexes."""

from whooshd.fake_token_step_backend import (
    FakeCancellationPlan,
    FakeFailurePlan,
    FakeSharedStepFailurePlan,
    FakeTimeoutPlan,
    FakeTokenStepBackend,
    TokenStepRequest,
)
from whooshd.token_step_scheduler import run_fake_token_step_schedule


class TestSamplerIsolation:
    def test_sampler_does_not_bleed(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 2, sampler_label="cold"),
             TokenStepRequest("b", "lb", 2, sampler_label="warm")],
            backend,
        )
        assert "cold" in results[0].content
        assert "warm" in results[1].content
        assert report.demux_bleed_detected is False
        assert report.sampler_states_observed == 2


class TestCancellationIsolation:
    def test_cancel_one_other_continues(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4)],
            backend,
            cancellation_plan=[FakeCancellationPlan("a", 2)],
        )
        assert results[0].finish_reason == "cancelled"
        assert results[1].finish_reason == "stop"
        assert report.sequences_cancelled == 1
        assert report.sequences_cleaned_up == 2
        assert report.demux_bleed_detected is False


class TestTimeoutIsolation:
    def test_timeout_one_other_continues(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4)],
            backend,
            timeout_plan=[FakeTimeoutPlan("a", 2)],
        )
        assert results[0].finish_reason == "timeout"
        assert results[1].finish_reason == "stop"
        assert report.sequences_timed_out == 1
        assert report.sequences_cleaned_up == 2


class TestFailureIsolation:
    def test_fail_one_other_continues(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4)],
            backend,
            failure_plan=[FakeFailurePlan("a", 2)],
        )
        assert results[0].finish_reason == "error"
        assert results[1].finish_reason == "stop"
        assert report.sequences_failed == 1
        assert report.sequences_cleaned_up == 2

    def test_shared_step_failure_fails_all_active(self):
        backend = FakeTokenStepBackend()
        results, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4)],
            backend,
            shared_step_failure_plan=[FakeSharedStepFailurePlan(2)],
        )
        assert results[0].finish_reason == "error"
        assert results[1].finish_reason == "error"
        assert report.shared_step_failures_observed == 1
        assert report.sequences_failed == 2
        assert report.sequences_cleaned_up == 2


class TestCleanupExactlyOnce:
    def test_mixed_terminal_states_cleanup(self):
        backend = FakeTokenStepBackend()
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4),
             TokenStepRequest("c", "lc", 4), TokenStepRequest("d", "ld", 4)],
            backend,
            cancellation_plan=[FakeCancellationPlan("a", 2)],
            timeout_plan=[FakeTimeoutPlan("b", 2)],
            failure_plan=[FakeFailurePlan("c", 2)],
        )
        assert report.sequences_cleaned_up == 4
        assert report.cleanup_exactly_once is True


class TestNoDemuxBleedMixed:
    def test_no_bleed_under_mixed_states(self):
        backend = FakeTokenStepBackend()
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 4), TokenStepRequest("b", "lb", 4),
             TokenStepRequest("c", "lc", 4), TokenStepRequest("d", "ld", 4)],
            backend,
            cancellation_plan=[FakeCancellationPlan("a", 2)],
            failure_plan=[FakeFailurePlan("c", 1)],
        )
        assert report.demux_bleed_detected is False


class TestReportPrivacy:
    def test_report_metadata_only(self):
        backend = FakeTokenStepBackend()
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 2, sampler_label="cold"),
             TokenStepRequest("b", "lb", 2, sampler_label="warm")],
            backend,
            cancellation_plan=[FakeCancellationPlan("a", 1)],
        )
        s = str(report)
        for f in ("raw_prompt", "generated_text_full", "token_ids_list",
                   "kv_handle", "cache_repr", "traceback", "cold", "warm"):
            assert f not in s.lower()

    def test_not_production(self):
        _, report = run_fake_token_step_schedule(
            [TokenStepRequest("a", "la", 1)], FakeTokenStepBackend(),
        )
        assert report.production_ready is False
        assert report.performance_claim_made is False

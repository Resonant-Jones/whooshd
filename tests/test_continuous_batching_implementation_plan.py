"""Tests for continuous batching implementation plan — decision enforcement."""

from whooshd.continuous_batching_plan import (
    ContinuousBatchingImplementationDecision,
    ContinuousBatchingTrack,
)


class TestDecision:
    def test_recommended_is_guarded_adapter_batch(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.recommended_track == ContinuousBatchingTrack.GUARDED_ADAPTER_BATCH

    def test_future_is_token_step(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.future_track == ContinuousBatchingTrack.TOKEN_STEP_SHARED_DECODE

    def test_not_production_ready(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.production_ready is False

    def test_no_performance_claims(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.performance_claim_allowed is False

    def test_no_default_enablement(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.default_enablement_allowed is False

    def test_no_token_step_claim(self):
        d = ContinuousBatchingImplementationDecision()
        assert d.token_step_claim_allowed is False

    def test_has_required_followups(self):
        d = ContinuousBatchingImplementationDecision()
        assert len(d.required_followups) > 0

    def test_report_metadata_only(self):
        d = ContinuousBatchingImplementationDecision()
        s = str(d)
        for f in ("raw_prompt", "token_ids", "generated_text", "cache", "model_repr", "traceback"):
            assert f not in s.lower()

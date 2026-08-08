"""Tests for snapshot policy engine."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from whooshd.runtime.threadwake.policy import (
    SnapshotEligibility,
    SnapshotEligibilityReason,
    SnapshotPolicyConfig,
    SnapshotPolicyEngine,
)
from whooshd.runtime.threadwake.replay_analysis import (
    CandidateReplayRecord,
    CandidateReplaySummary,
)


def _record(**overrides):
    defaults = {
        "prefix_hash": "abc", "backend": "mlx", "model_id": "m",
        "seen_count": 10, "average_candidate_score": 0.90,
        "average_potential_saved_ratio": 0.75,
        "potential_saved_tokens_total": 3000,
        "confidence": "high",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(overrides)
    return CandidateReplayRecord(**defaults)


class TestEligibility:
    def test_policy_disabled_rejects(self):
        config = SnapshotPolicyConfig(enabled=False)
        engine = SnapshotPolicyEngine(config)
        result = engine.evaluate_candidate(_record())
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.POLICY_DISABLED.value

    def test_unsupported_backend_rejects(self):
        result = SnapshotPolicyEngine().evaluate_candidate(_record(backend="llama_cpp"))
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.UNSUPPORTED_BACKEND.value

    def test_low_seen_count_rejects(self):
        result = SnapshotPolicyEngine().evaluate_candidate(_record(seen_count=2))
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.INSUFFICIENT_OBSERVATIONS.value

    def test_low_score_rejects(self):
        result = SnapshotPolicyEngine().evaluate_candidate(
            _record(seen_count=10, average_candidate_score=0.5))
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.LOW_VALUE.value

    def test_low_saved_ratio_rejects(self):
        result = SnapshotPolicyEngine().evaluate_candidate(
            _record(average_potential_saved_ratio=0.2))
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.LOW_VALUE.value

    def test_expired_rejects(self):
        result = SnapshotPolicyEngine().evaluate_candidate(
            _record(last_seen_at="2020-01-01T00:00:00+00:00"))
        assert result.eligible is False
        assert result.reason == SnapshotEligibilityReason.EXPIRED.value

    def test_high_value_passes(self):
        result = SnapshotPolicyEngine().evaluate_candidate(_record())
        assert result.eligible is True
        assert result.reason == SnapshotEligibilityReason.HIGH_FREQUENCY_HIGH_SAVINGS.value
        assert result.policy_version == "1"

    def test_high_frequency_passes(self):
        result = SnapshotPolicyEngine().evaluate_candidate(
            _record(seen_count=15, potential_saved_tokens_total=500))
        assert result.eligible is True
        assert result.reason == SnapshotEligibilityReason.HIGH_FREQUENCY.value

    def test_high_savings_passes(self):
        result = SnapshotPolicyEngine().evaluate_candidate(
            _record(seen_count=6, potential_saved_tokens_total=5000))
        assert result.eligible is True
        assert result.reason == SnapshotEligibilityReason.HIGH_SAVINGS.value


class TestReplaySummary:
    def test_evaluates_all_top_candidates(self):
        summary = CandidateReplaySummary(
            top_candidates=[
                _record(prefix_hash="h1", seen_count=10, average_candidate_score=0.90),
                _record(prefix_hash="h2", seen_count=3, average_candidate_score=0.85),
            ],
        )
        results = SnapshotPolicyEngine().evaluate_replay_summary(summary)
        assert len(results) == 2
        assert results[0].eligible is True
        assert results[1].eligible is False


class TestStats:
    def test_tracks_evaluations(self):
        engine = SnapshotPolicyEngine()
        engine.evaluate_candidate(_record())
        engine.evaluate_candidate(_record(seen_count=2))
        s = engine.policy_stats()
        assert s["evaluations_total"] == 2
        assert s["eligible_total"] == 1
        assert s["rejected_total"] == 1

    def test_rejection_reasons_counted(self):
        engine = SnapshotPolicyEngine()
        engine.evaluate_candidate(_record(backend="unsupported"))
        engine.evaluate_candidate(_record(backend="unsupported"))
        s = engine.policy_stats()
        assert s["rejection_reasons"]["unsupported_backend"] == 2


class TestDeterministic:
    def test_same_input_same_output(self):
        engine = SnapshotPolicyEngine()
        r1 = engine.evaluate_candidate(_record())
        r2 = engine.evaluate_candidate(_record())
        assert r1.eligible == r2.eligible
        assert r1.reason == r2.reason


class TestSafeDict:
    def test_no_raw_token_ids(self):
        result = SnapshotPolicyEngine().evaluate_candidate(_record())
        d = result.safe_dict()
        assert "token_ids" not in json.dumps(d)

    def test_config_dict(self):
        config = SnapshotPolicyConfig()
        d = config.to_dict()
        assert "supported_backends" in d
        assert "mlx" in d["supported_backends"]


class TestConfigDefaults:
    def test_default_minimums_are_conservative(self):
        config = SnapshotPolicyConfig()
        assert config.minimum_seen_count == 5
        assert config.minimum_candidate_score == 0.80
        assert config.minimum_saved_ratio == 0.50
        assert config.maximum_candidate_age_days == 30
        assert "mlx" in config.supported_backends

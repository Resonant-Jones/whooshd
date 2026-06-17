"""Tests for candidate KV snapshot selection."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.candidate_selection import (
    CandidateConfidence,
    CandidateScore,
    CandidateSelectionReason,
    SnapshotCandidate,
    SnapshotCandidateSelector,
    SnapshotSelectionResult,
)
from whooshd.runtime.threadwake.prefix_proof import PrefixProof


class MockProof:
    def __init__(self, **kwargs):
        self.compatible = kwargs.get("compatible", True)
        self.shared_prefix_tokens = kwargs.get("shared_prefix_tokens", 1000)
        self.prefix_hash = kwargs.get("prefix_hash", "abc123")
        self.model_id = kwargs.get("model_id", "test-model")
        self.backend = kwargs.get("backend", "fake")
        self.tokenizer_hash = kwargs.get("tokenizer_hash", "tok-001")
        self.chat_template_hash = kwargs.get("chat_template_hash", "tmpl-001")
        self.reason = kwargs.get("reason", None)


# ── Selection ─────────────────────────────────────────────────────────────


class TestSelection:
    def test_compatible_proof_above_threshold_selects(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=256)
        proof = MockProof(shared_prefix_tokens=500, compatible=True)
        result = selector.evaluate(proof, total_tokens=1000)
        assert result.selected is True
        assert result.candidate is not None
        assert result.candidate.potential_saved_tokens == 500
        assert result.reason == CandidateSelectionReason.PROOF_COMPATIBLE.value

    def test_below_threshold_rejects(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=1000)
        proof = MockProof(shared_prefix_tokens=500, compatible=True)
        result = selector.evaluate(proof, total_tokens=1000)
        assert result.selected is False
        assert result.reason == CandidateSelectionReason.SHARED_PREFIX_BELOW_THRESHOLD.value

    def test_incompatible_proof_rejects(self):
        selector = SnapshotCandidateSelector()
        proof = MockProof(compatible=False)
        result = selector.evaluate(proof, total_tokens=1000)
        assert result.selected is False
        assert result.reason == CandidateSelectionReason.INCOMPATIBLE_PROOF.value

    def test_missing_proof_rejects(self):
        selector = SnapshotCandidateSelector()
        result = selector.evaluate(None, total_tokens=1000)
        assert result.selected is False
        assert result.reason == CandidateSelectionReason.MISSING_PROOF.value

    def test_empty_total_tokens_rejects(self):
        selector = SnapshotCandidateSelector()
        proof = MockProof(compatible=True, shared_prefix_tokens=100)
        result = selector.evaluate(proof, total_tokens=0)
        assert result.selected is False
        assert result.reason == CandidateSelectionReason.EMPTY_PROMPT.value

    def test_ratio_computed_correctly(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=1)
        proof = MockProof(shared_prefix_tokens=300, compatible=True)
        result = selector.evaluate(proof, total_tokens=1000)
        assert result.candidate is not None
        assert result.candidate.potential_saved_ratio == 0.3
        assert result.candidate.dynamic_tail_tokens == 700
        assert result.candidate.total_tokens == 1000

    def test_dynamic_tail_tokens_floor_zero(self):
        """dynamic_tail_tokens must not go negative."""
        selector = SnapshotCandidateSelector(min_prefix_tokens=1)
        proof = MockProof(shared_prefix_tokens=1500, compatible=True)
        result = selector.evaluate(proof, total_tokens=1000)
        assert result.candidate is not None
        assert result.candidate.dynamic_tail_tokens == 0


# ── Scoring ────────────────────────────────────────────────────────────────


class TestScoring:
    def test_score_is_deterministic(self):
        candidate = SnapshotCandidate(
            candidate_id="c1", prefix_hash="h",
            shared_prefix_tokens=2000, total_tokens=4000,
            potential_saved_tokens=2000, potential_saved_ratio=0.5,
        )
        s1 = SnapshotCandidateSelector.score_candidate(candidate)
        s2 = SnapshotCandidateSelector.score_candidate(candidate)
        assert s1.score == s2.score

    def test_high_confidence(self):
        candidate = SnapshotCandidate(
            candidate_id="c1", prefix_hash="h",
            shared_prefix_tokens=5000, total_tokens=6000,
            potential_saved_tokens=5000, potential_saved_ratio=0.8333,
        )
        score = SnapshotCandidateSelector.score_candidate(candidate)
        assert score.confidence == CandidateConfidence.HIGH.value

    def test_medium_confidence(self):
        candidate = SnapshotCandidate(
            candidate_id="c1", prefix_hash="h",
            shared_prefix_tokens=2000, total_tokens=3000,
            potential_saved_tokens=2000, potential_saved_ratio=0.6667,
        )
        score = SnapshotCandidateSelector.score_candidate(candidate)
        assert score.confidence == CandidateConfidence.MEDIUM.value

    def test_low_confidence(self):
        candidate = SnapshotCandidate(
            candidate_id="c1", prefix_hash="h",
            shared_prefix_tokens=500, total_tokens=2000,
            potential_saved_tokens=500, potential_saved_ratio=0.25,
        )
        score = SnapshotCandidateSelector.score_candidate(candidate)
        assert score.confidence == CandidateConfidence.LOW.value

    def test_score_in_range(self):
        """Score must be in [0, 1]."""
        for shared in [100, 500, 2000, 10000]:
            candidate = SnapshotCandidate(
                candidate_id="c", prefix_hash="h",
                shared_prefix_tokens=shared, total_tokens=shared + 1000,
                potential_saved_tokens=shared,
                potential_saved_ratio=shared / (shared + 1000),
            )
            score = SnapshotCandidateSelector.score_candidate(candidate)
            assert 0.0 <= score.score <= 1.0


# ── Serialization safety ──────────────────────────────────────────────────


class TestSerialization:
    def test_candidate_safe_dict_no_token_ids(self):
        candidate = SnapshotCandidate(
            candidate_id="c1", prefix_hash="abc",
            shared_prefix_tokens=100, total_tokens=200,
        )
        d = candidate.safe_dict()
        assert "token_ids" not in d

    def test_result_safe_dict_no_token_ids(self):
        result = SnapshotSelectionResult(selected=True, reason="ok")
        d = result.safe_dict()
        assert "token_ids" not in json.dumps(d)

    def test_score_safe_dict_no_token_ids(self):
        score = CandidateScore(score=0.5, confidence="high")
        d = score.safe_dict()
        assert "token_ids" not in json.dumps(d)

    def test_all_safe_dicts_json_serializable(self):
        candidate = SnapshotCandidate(candidate_id="c", prefix_hash="h", shared_prefix_tokens=100)
        score = CandidateScore(score=0.5, confidence="high")
        result = SnapshotSelectionResult(selected=True, candidate=candidate, score=score)
        json.dumps(result.safe_dict())


# ── Stats ─────────────────────────────────────────────────────────────────


class TestStats:
    def test_stats_track_evaluations(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=1)
        proof = MockProof(shared_prefix_tokens=100, compatible=True)
        selector.evaluate(proof, total_tokens=500)
        selector.evaluate(proof, total_tokens=500)
        s = selector.stats()
        assert s["evaluations"] == 2
        assert s["selected"] == 2

    def test_stats_track_rejected(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=10000)
        proof = MockProof(shared_prefix_tokens=100, compatible=True)
        selector.evaluate(proof, total_tokens=500)
        s = selector.stats()
        assert s["rejected"] == 1

    def test_stats_track_saved_tokens(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=1)
        proof = MockProof(shared_prefix_tokens=300, compatible=True)
        selector.evaluate(proof, total_tokens=1000)
        s = selector.stats()
        assert s["potential_saved_tokens_total"] == 300

    def test_reset_clears_stats(self):
        selector = SnapshotCandidateSelector(min_prefix_tokens=1)
        proof = MockProof(shared_prefix_tokens=100, compatible=True)
        selector.evaluate(proof, total_tokens=500)
        selector.reset()
        assert selector.stats()["evaluations"] == 0

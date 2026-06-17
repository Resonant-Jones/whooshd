"""Tests for candidate replay analysis."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex
from whooshd.runtime.threadwake.replay_analysis import (
    CandidateReplayAnalyzer,
    CandidateReplayRecord,
    CandidateReplaySummary,
)
from whooshd.runtime.threadwake.storage import NoOpThreadWakeStorage


def _build_index_with_candidates() -> ThreadWakeIndex:
    index = ThreadWakeIndex(max_entries=50)
    for i in range(5):
        key = f"key-{i}"
        index.put_observation(
            cache_key=key, model_id="m", backend="fake",
            prompt_prefix_hash=f"hash-{i}", token_count=100 + i * 50,
            scope="thread", scope_context=ScopeContext(thread_id="t1"),
        )
        conf = ["high", "medium", "low", "high", "medium"][i]
        score = [0.9, 0.7, 0.3, 0.85, 0.6][i]
        saved = [1000, 500, 200, 800, 400][i]
        index.mark_candidate_selected(key, score=score, confidence=conf,
            selection_reason="proof", potential_saved_tokens=saved,
            potential_saved_ratio=score)
    return index


class TestAnalyzeIndex:
    def test_empty_index_returns_empty_summary(self):
        index = ThreadWakeIndex()
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index)
        assert summary.total_candidates == 0
        assert summary.top_candidates == []

    def test_counts_candidates_correctly(self):
        index = _build_index_with_candidates()
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index)
        assert summary.total_candidates == 5
        assert summary.total_seen_count == 5
        assert summary.high_confidence_candidates == 2
        assert summary.medium_confidence_candidates == 2
        assert summary.low_confidence_candidates == 1

    def test_total_potential_saved_tokens(self):
        index = _build_index_with_candidates()
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index)
        assert summary.total_potential_saved_tokens == 1000 + 500 + 200 + 800 + 400

    def test_ranks_by_seen_count_and_score(self):
        index = _build_index_with_candidates()
        # Mark one candidate multiple times to boost its seen count
        index.mark_candidate_selected("key-0", score=0.9, confidence="high",
            selection_reason="proof", potential_saved_tokens=1000, potential_saved_ratio=0.9)
        index.mark_candidate_selected("key-0", score=0.9, confidence="high",
            selection_reason="proof", potential_saved_tokens=1000, potential_saved_ratio=0.9)
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index)
        # key-0 should be first (seen_count=3, high score)
        assert len(summary.top_candidates) >= 1
        top = summary.top_candidates[0]
        assert top.prefix_hash == "hash-0"

    def test_limit_is_respected(self):
        index = _build_index_with_candidates()
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index, limit=2)
        assert len(summary.top_candidates) <= 2

    def test_no_raw_content(self):
        index = _build_index_with_candidates()
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_index(index)
        d = summary.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)
        for c in d["top_candidates"]:
            assert "token_ids" not in json.dumps(c)


class TestAnalyzeStorage:
    def test_noop_storage_returns_empty(self):
        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_storage(NoOpThreadWakeStorage())
        assert summary.total_candidates == 0

    def test_sqlite_storage_analyzes(self):
        import tempfile, os
        from whooshd.runtime.threadwake.storage import SQLiteThreadWakeStorage
        from whooshd.runtime.threadwake.index import ThreadWakeIndexEntry

        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        s = SQLiteThreadWakeStorage(path)

        entry = ThreadWakeIndexEntry(
            cache_key="k", model_id="m", backend="fake",
            prompt_prefix_hash="hash-a", token_count=100,
            candidate_score=0.9, candidate_confidence="high",
            potential_saved_tokens=500, potential_saved_ratio=0.5,
            selection_reason="proof",
        )
        s.upsert_candidate(entry)

        analyzer = CandidateReplayAnalyzer()
        summary = analyzer.analyze_storage(s)
        assert summary.total_candidates == 1
        assert summary.high_confidence_candidates == 1

        s.close()
        os.unlink(path)


class TestRanking:
    def test_deterministic_ranking(self):
        records = [
            {"prefix_hash": "a", "candidate_seen_count": 5, "potential_saved_tokens": 500, "candidate_score": 0.9},
            {"prefix_hash": "b", "candidate_seen_count": 2, "potential_saved_tokens": 200, "candidate_score": 0.5},
        ]
        r1 = CandidateReplayAnalyzer.rank_candidates(records)
        r2 = CandidateReplayAnalyzer.rank_candidates(records)
        assert [c.prefix_hash for c in r1] == [c.prefix_hash for c in r2]
        assert r1[0].prefix_hash == "a"


class TestSafeDict:
    def test_summary_json_serializable(self):
        summary = CandidateReplaySummary(
            total_candidates=2,
            top_candidates=[
                CandidateReplayRecord(prefix_hash="h1", seen_count=5),
                CandidateReplayRecord(prefix_hash="h2", seen_count=2),
            ],
        )
        json.dumps(summary.safe_dict())

    def test_record_json_serializable(self):
        r = CandidateReplayRecord(prefix_hash="h", seen_count=10, confidence="high")
        json.dumps(r.safe_dict())

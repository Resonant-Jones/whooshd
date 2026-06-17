"""Tests for candidate selection metadata on ThreadWakeIndex."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from whooshd.runtime.threadwake.index import (
    EntryStatus,
    ScopeContext,
    ThreadWakeIndex,
    ThreadWakeIndexEntry,
)


def _put(index, cache_key="key-1", **kwargs):
    defaults = {
        "cache_key": cache_key, "model_id": "m", "backend": "fake",
        "prompt_prefix_hash": "abc", "token_count": 100,
        "scope": "thread", "scope_context": ScopeContext(thread_id="t1"),
    }
    defaults.update(kwargs)
    return index.put_observation(**defaults)


class TestEntryDefaults:
    def test_new_entry_candidate_metadata_is_none(self):
        index = ThreadWakeIndex()
        entry = _put(index)
        assert entry.candidate_score is None
        assert entry.candidate_confidence is None
        assert entry.candidate_selected_at is None
        assert entry.selection_reason is None
        assert entry.potential_saved_tokens is None
        assert entry.potential_saved_ratio is None
        assert entry.candidate_seen_count == 0

    def test_entry_public_snapshot_includes_candidate_fields(self):
        index = ThreadWakeIndex()
        _put(index)
        index.mark_candidate_selected("key-1", score=0.8, confidence="high",
            selection_reason="proof_compatible", potential_saved_tokens=500,
            potential_saved_ratio=0.5)
        snap = index.list_candidates()
        assert len(snap) == 1
        assert snap[0]["candidate_score"] == 0.8
        assert snap[0]["candidate_confidence"] == "high"
        assert snap[0]["potential_saved_tokens"] == 500


class TestMarkCandidate:
    def test_unknown_key_returns_false(self):
        index = ThreadWakeIndex()
        result = index.mark_candidate_selected("nonexistent", score=0.5,
            confidence="low", selection_reason="proof", potential_saved_tokens=100,
            potential_saved_ratio=0.3)
        assert result is False

    def test_sets_all_fields(self):
        index = ThreadWakeIndex()
        _put(index)
        now = datetime.now(timezone.utc)
        result = index.mark_candidate_selected("key-1", score=0.9,
            confidence="high", selection_reason="proof_compatible",
            potential_saved_tokens=1000, potential_saved_ratio=0.75,
            selected_at=now)
        assert result is True
        entry = index.get("key-1", ScopeContext(thread_id="t1"))
        assert entry is not None
        assert entry.candidate_score == 0.9
        assert entry.candidate_confidence == "high"
        assert entry.selection_reason == "proof_compatible"
        assert entry.potential_saved_tokens == 1000
        assert entry.potential_saved_ratio == 0.75
        assert entry.candidate_selected_at == now

    def test_increments_seen_count(self):
        index = ThreadWakeIndex()
        _put(index)
        index.mark_candidate_selected("key-1", score=0.5, confidence="medium",
            selection_reason="proof", potential_saved_tokens=100,
            potential_saved_ratio=0.3)
        index.mark_candidate_selected("key-1", score=0.6, confidence="medium",
            selection_reason="proof", potential_saved_tokens=200,
            potential_saved_ratio=0.4)
        entry = index.get("key-1", ScopeContext(thread_id="t1"))
        assert entry is not None
        assert entry.candidate_seen_count == 2

    def test_updates_last_seen(self):
        index = ThreadWakeIndex()
        _put(index)
        index.mark_candidate_selected("key-1", score=0.5, confidence="low",
            selection_reason="proof", potential_saved_tokens=50,
            potential_saved_ratio=0.1)
        first = index.get("key-1", ScopeContext(thread_id="t1"))
        assert first is not None
        assert first.candidate_last_seen_at is not None
        index.mark_candidate_selected("key-1", score=0.6, confidence="low",
            selection_reason="proof", potential_saved_tokens=60,
            potential_saved_ratio=0.2)
        second = index.get("key-1", ScopeContext(thread_id="t1"))
        assert second is not None
        assert second.candidate_last_seen_at >= first.candidate_last_seen_at

    def test_does_not_alter_lifecycle(self):
        index = ThreadWakeIndex()
        _put(index)
        assert index.get("key-1", ScopeContext(thread_id="t1")).status == EntryStatus.OBSERVED
        index.mark_candidate_selected("key-1", score=0.5, confidence="low",
            selection_reason="proof", potential_saved_tokens=100,
            potential_saved_ratio=0.3)
        assert index.get("key-1", ScopeContext(thread_id="t1")).status == EntryStatus.OBSERVED


class TestListCandidates:
    def test_returns_only_candidates(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a")
        _put(index, "key-b")
        index.mark_candidate_selected("key-a", score=0.8, confidence="high",
            selection_reason="proof", potential_saved_tokens=500,
            potential_saved_ratio=0.5)
        candidates = index.list_candidates()
        assert len(candidates) == 1
        assert candidates[0]["cache_key"] == "key-a"

    def test_sorts_by_score_desc(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a"); _put(index, "key-b"); _put(index, "key-c")
        index.mark_candidate_selected("key-a", score=0.3, confidence="low",
            selection_reason="p", potential_saved_tokens=10, potential_saved_ratio=0.1)
        index.mark_candidate_selected("key-b", score=0.9, confidence="high",
            selection_reason="p", potential_saved_tokens=100, potential_saved_ratio=0.5)
        index.mark_candidate_selected("key-c", score=0.6, confidence="medium",
            selection_reason="p", potential_saved_tokens=50, potential_saved_ratio=0.3)
        candidates = index.list_candidates()
        assert candidates[0]["cache_key"] == "key-b"
        assert candidates[1]["cache_key"] == "key-c"

    def test_filters_by_confidence(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a"); _put(index, "key-b")
        index.mark_candidate_selected("key-a", score=0.8, confidence="high",
            selection_reason="p", potential_saved_tokens=100, potential_saved_ratio=0.5)
        index.mark_candidate_selected("key-b", score=0.5, confidence="low",
            selection_reason="p", potential_saved_tokens=50, potential_saved_ratio=0.2)
        high = index.list_candidates(min_confidence="high")
        assert len(high) == 1
        assert high[0]["cache_key"] == "key-a"

    def test_filters_by_backend(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a", backend="mlx")
        _put(index, "key-b", backend="fake")
        index.mark_candidate_selected("key-a", score=0.8, confidence="high",
            selection_reason="p", potential_saved_tokens=100, potential_saved_ratio=0.5)
        index.mark_candidate_selected("key-b", score=0.5, confidence="low",
            selection_reason="p", potential_saved_tokens=50, potential_saved_ratio=0.2)
        mlx_only = index.list_candidates(backend="mlx")
        assert len(mlx_only) == 1
        assert mlx_only[0]["backend"] == "mlx"

    def test_caps_limit(self):
        index = ThreadWakeIndex(max_entries=50)
        for i in range(10):
            _put(index, f"key-{i}")
            index.mark_candidate_selected(f"key-{i}", score=0.5, confidence="low",
                selection_reason="p", potential_saved_tokens=10, potential_saved_ratio=0.1)
        result = index.list_candidates(limit=3)
        assert len(result) <= 3

    def test_safe_output_no_raw_token_ids(self):
        index = ThreadWakeIndex()
        _put(index)
        index.mark_candidate_selected("key-1", score=0.8, confidence="high",
            selection_reason="proof", potential_saved_tokens=500,
            potential_saved_ratio=0.5)
        result = index.list_candidates()
        assert len(result) > 0
        assert "token_ids" not in json.dumps(result[0])
        assert "opaque_ref" not in json.dumps(result[0])


class TestCandidateStats:
    def test_empty_returns_zeros(self):
        index = ThreadWakeIndex()
        stats = index.candidate_stats()
        assert stats["total_candidates"] == 0
        assert stats["high_confidence"] == 0
        assert stats["average_candidate_score"] == 0.0

    def test_counts_confidence_levels(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a"); _put(index, "key-b"); _put(index, "key-c")
        index.mark_candidate_selected("key-a", score=0.8, confidence="high",
            selection_reason="p", potential_saved_tokens=100, potential_saved_ratio=0.5)
        index.mark_candidate_selected("key-b", score=0.5, confidence="medium",
            selection_reason="p", potential_saved_tokens=200, potential_saved_ratio=0.3)
        index.mark_candidate_selected("key-c", score=0.2, confidence="low",
            selection_reason="p", potential_saved_tokens=300, potential_saved_ratio=0.1)
        stats = index.candidate_stats()
        assert stats["total_candidates"] == 3
        assert stats["high_confidence"] == 1
        assert stats["medium_confidence"] == 1
        assert stats["low_confidence"] == 1
        assert stats["potential_saved_tokens_total"] == 600
        assert stats["candidate_seen_total"] == 3

    def test_average_score(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "a"); _put(index, "b")
        index.mark_candidate_selected("a", score=0.8, confidence="high",
            selection_reason="p", potential_saved_tokens=100, potential_saved_ratio=0.5)
        index.mark_candidate_selected("b", score=0.4, confidence="low",
            selection_reason="p", potential_saved_tokens=50, potential_saved_ratio=0.2)
        stats = index.candidate_stats()
        assert stats["average_candidate_score"] == 0.6

"""Tests for ThreadWakeIndex — put/get, LRU eviction, flush, stats, list_entries."""

from __future__ import annotations

import pytest

from whooshd.runtime.threadwake.index import (
    EntryStatus,
    ScopeContext,
    ThreadWakeIndex,
    ThreadWakeIndexEntry,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _ctx(thread_id: str | None = None, user_id: str | None = None) -> ScopeContext:
    return ScopeContext(thread_id=thread_id, user_id=user_id)


THREAD_A = _ctx(thread_id="thread-a")
THREAD_B = _ctx(thread_id="thread-b")
USER_A = _ctx(user_id="user-a")


def _put(index: ThreadWakeIndex, cache_key: str = "key-1", **kwargs) -> ThreadWakeIndexEntry:
    defaults = {
        "cache_key": cache_key,
        "model_id": "test-model",
        "backend": "stub",
        "prompt_prefix_hash": "abc123",
        "token_count": 100,
        "scope": "thread",
        "scope_context": THREAD_A,
    }
    defaults.update(kwargs)
    return index.put_observation(**defaults)


# ── put / get ──────────────────────────────────────────────────────────────


class TestPutGet:
    def test_put_creates_observed_entry(self):
        index = ThreadWakeIndex()
        entry = _put(index)
        assert entry.status == EntryStatus.OBSERVED
        assert entry.cache_key == "key-1"
        assert entry.model_id == "test-model"

    def test_get_returns_none_for_missing_key(self):
        index = ThreadWakeIndex()
        result = index.get("nonexistent", THREAD_A)
        assert result is None

    def test_get_returns_entry_with_matching_scope(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        result = index.get("key-1", THREAD_A)
        assert result is not None
        assert result.cache_key == "key-1"

    def test_get_increments_hit_count(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        index.get("key-1", THREAD_A)
        index.get("key-1", THREAD_A)
        entry = index.get("key-1", THREAD_A)
        assert entry is not None
        assert entry.hit_count == 3

    def test_put_updates_existing_entry(self):
        index = ThreadWakeIndex()
        first = _put(index, "key-1", token_count=100)
        second = _put(index, "key-1", token_count=200)
        assert first.cache_key == second.cache_key
        assert second.token_count == 200

    def test_stale_entry_not_returned_as_ready(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        index.mark_stale("key-1")
        result = index.get("key-1", THREAD_A)
        assert result is None


# ── mark_ready / mark_stale ────────────────────────────────────────────────


class TestMarkTransitions:
    def test_mark_ready_transitions_from_observed(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")
        index.mark_ready("key-1")
        entry = index.get("key-1", THREAD_A)
        assert entry is not None
        assert entry.status == EntryStatus.READY

    def test_mark_stale_transitions_from_ready(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")
        index.mark_ready("key-1")
        index.mark_stale("key-1")
        # Stale entries are not returned by get()
        result = index.get("key-1", THREAD_A)
        assert result is None

    def test_mark_ready_nonexistent_is_noop(self):
        index = ThreadWakeIndex()
        index.mark_ready("nonexistent")  # Should not raise

    def test_mark_stale_nonexistent_is_noop(self):
        index = ThreadWakeIndex()
        index.mark_stale("nonexistent")  # Should not raise


# ── eviction ───────────────────────────────────────────────────────────────


class TestEviction:
    def test_evict_removes_entry(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")
        index.evict("key-1")
        result = index.get("key-1", THREAD_A)
        assert result is None

    def test_evict_nonexistent_is_noop(self):
        index = ThreadWakeIndex()
        index.evict("nonexistent")  # Should not raise

    def test_lru_eviction_by_max_entries(self):
        index = ThreadWakeIndex(max_entries=3)
        for i in range(5):
            _put(index, f"key-{i}")

        stats = index.stats()
        assert stats.entry_count <= 3
        assert stats.evictions >= 2

    def test_lru_evicts_least_recently_used_first(self):
        index = ThreadWakeIndex(max_entries=2)
        _put(index, "key-a")
        _put(index, "key-b")
        # Touch key-a
        index.get("key-a", THREAD_A)
        # Add key-c — should evict key-b (LRU)
        _put(index, "key-c")
        assert index.get("key-a", THREAD_A) is not None
        assert index.get("key-b", THREAD_B) is None  # wrong scope anyway
        # Check that key-b is evicted via stats or list
        entries = index.list_entries()
        keys = [e["cache_key"] for e in entries]
        assert "key-a" in keys
        assert "key-c" in keys
        assert "key-b" not in keys

    def test_memory_based_eviction(self):
        index = ThreadWakeIndex(
            max_entries=1000,
            max_memory_bytes=500,
            bytes_per_token=10,
        )
        # Each entry: 30 tokens * 10 bytes = 300 bytes
        # First fits (300 <= 500), second pushes to 600 > 500 → evicts first
        _put(index, "key-a", token_count=30)
        _put(index, "key-b", token_count=30)
        entries = index.list_entries()
        keys = [e["cache_key"] for e in entries]
        # key-a should be evicted (memory cap exceeded)
        assert "key-a" not in keys
        assert "key-b" in keys


# ── flush ──────────────────────────────────────────────────────────────────


class TestFlush:
    def test_flush_all_removes_all_entries(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")
        _put(index, "key-2")
        count = index.flush()
        assert count == 2
        assert index.stats().entry_count == 0

    def test_flush_by_scope_removes_only_matching(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        _put(index, "key-2", scope="thread", scope_context=THREAD_A)
        _put(index, "key-3", scope="request", scope_context=ScopeContext())
        count = index.flush(scope="thread")
        assert count == 2
        stats = index.stats()
        assert stats.entry_count == 1

    def test_flush_unknown_scope_removes_none(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        count = index.flush(scope="project")
        assert count == 0
        assert index.stats().entry_count == 1

    def test_flush_empty_index_returns_zero(self):
        index = ThreadWakeIndex()
        count = index.flush()
        assert count == 0


# ── stats / list_entries ───────────────────────────────────────────────────


class TestStatsAndList:
    def test_stats_reflects_entry_counts(self):
        index = ThreadWakeIndex(max_entries=10)
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)
        _put(index, "key-2", scope="request", scope_context=ScopeContext())
        index.mark_ready("key-1")

        stats = index.stats()
        assert stats.entry_count == 2
        assert stats.max_entries == 10
        assert stats.hit_count == 0
        assert stats.evictions == 0
        assert "observed" in stats.entries_by_status
        assert "ready" in stats.entries_by_status

    def test_stats_do_not_include_raw_prompt_content(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")

        stats = index.stats()
        stats_dict = stats.to_dict()
        # Verify no raw prompt fields
        assert "content" not in str(stats_dict)
        assert "messages" not in str(stats_dict)
        assert "prompt" not in stats_dict  # only non-raw fields

    def test_list_entries_returns_public_snapshots(self):
        index = ThreadWakeIndex()
        _put(index, "key-1")
        _put(index, "key-2")

        entries = index.list_entries()
        assert len(entries) == 2
        for entry in entries:
            assert "cache_key" in entry
            assert "model_id" in entry
            # scope_id should not appear in public snapshots
            assert "scope_id" not in entry

    def test_list_entries_respects_limit(self):
        index = ThreadWakeIndex(max_entries=50)
        for i in range(10):
            _put(index, f"key-{i}")
        entries = index.list_entries(limit=3)
        assert len(entries) == 3

    def test_list_entries_sorted_by_last_used_desc(self):
        index = ThreadWakeIndex(max_entries=50)
        _put(index, "key-a")
        _put(index, "key-b")
        index.get("key-a", THREAD_A)  # touch key-a

        entries = index.list_entries()
        # key-a should be first (most recently used)
        assert entries[0]["cache_key"] == "key-a"

    def test_hit_miss_counters(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=THREAD_A)

        # Miss — wrong key
        index.get("nonexistent", THREAD_A)
        # Miss — wrong scope
        index.get("key-1", THREAD_B)
        # Hit
        index.get("key-1", THREAD_A)

        stats = index.stats()
        assert stats.hit_count == 1
        assert stats.miss_count == 2

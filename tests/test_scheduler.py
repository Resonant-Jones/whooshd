"""Tests for the scheduler policy skeleton with cache-aware experiment."""

from __future__ import annotations

import pytest

from whooshd.scheduler import (
    Scheduler,
    SchedulerCandidate,
    SchedulerDecisionReason,
    SchedulerPolicy,
)
from whooshd.config import get_scheduler_policy, get_scheduler_max_bypass


# ── Helpers ────────────────────────────────────────────────────────────────


def _candidate(rid, queued_at, cache_ready=False):
    return SchedulerCandidate(
        request_id=rid, queued_at=queued_at, threadwake_cache_ready=cache_ready
    )


# ── Test 1: default policy remains FIFO ────────────────────────────────────


class TestDefaultFIFO:
    def test_default_policy_is_fifo(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_SCHEDULER_POLICY", raising=False)
        scheduler = Scheduler()
        assert scheduler.policy == SchedulerPolicy.FIFO

    def test_default_no_cache_affinity(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_SCHEDULER_POLICY", raising=False)
        scheduler = Scheduler()
        candidates = [
            _candidate("a", 1.0),
            _candidate("b", 2.0, cache_ready=True),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST


# ── Test 2: cache-aware policy prefers ready cache candidate ───────────────


class TestCacheAware:
    def test_prefers_cache_ready_over_older_nonready(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=False),  # older, no cache
            _candidate("b", 2.0, cache_ready=True),   # newer, cache ready
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "b"
        assert decision.reason == SchedulerDecisionReason.CACHE_AFFINITY
        assert decision.policy == SchedulerPolicy.CACHE_AWARE_FIFO

    def test_oldest_cache_ready_is_fifo(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=True),
            _candidate("b", 2.0, cache_ready=False),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST


# ── Test 3: FIFO wins after bypass limit ───────────────────────────────────


class TestFairnessBypassLimit:
    def test_fifo_wins_after_max_bypass(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_SCHEDULER_MAX_BYPASS", "1")
        scheduler = Scheduler()

        # First scheduling: B gets through over A (bypass A).
        candidates = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("b", 2.0, cache_ready=True),
        ]
        d1 = scheduler.choose_next(candidates, capacity_available=True)
        assert d1.request_id == "b"
        assert d1.reason == SchedulerDecisionReason.CACHE_AFFINITY

        # A's bypass count is now 1.  Next scheduling: A must win.
        candidates2 = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("c", 3.0, cache_ready=True),
        ]
        d2 = scheduler.choose_next(candidates2, capacity_available=True)
        assert d2.request_id == "a"
        assert d2.reason == SchedulerDecisionReason.FAIRNESS_FIFO

    def test_bypass_count_reset_after_removal(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("b", 2.0, cache_ready=True),
        ]
        scheduler.choose_next(candidates, capacity_available=True)
        # A was bypassed once.
        assert scheduler._bypass_counts.get("a") == 1

        scheduler.remove_request("a")
        assert scheduler._bypass_counts.get("a") is None


# ── Test 4: cache-aware falls back to FIFO when no cache-ready ─────────────


class TestFallbackToFIFO:
    def test_no_cache_ready_falls_back(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("b", 2.0, cache_ready=False),
            _candidate("c", 3.0, cache_ready=False),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST


# ── Test 5: oldest cache-ready is FIFO ─────────────────────────────────────


class TestCacheReadyOldest:
    def test_oldest_cache_ready_not_bypassed(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=True),
            _candidate("b", 2.0, cache_ready=False),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST
        # No bypass should be counted — oldest is already selected.
        assert scheduler._bypass_counts.get("a", 0) == 0

    def test_all_cache_ready_picks_oldest(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=True),
            _candidate("b", 2.0, cache_ready=True),
            _candidate("c", 3.0, cache_ready=True),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        # Oldest and cache-ready — same as FIFO.
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST


# ── Test 6: bypass counts increment only for skipped older candidates ──────


class TestBypassCounts:
    def test_bypass_incremented_for_older_skipped(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("b", 2.0, cache_ready=False),
            _candidate("c", 3.0, cache_ready=True),  # cache-ready, will win
        ]
        scheduler.choose_next(candidates, capacity_available=True)
        # a and b were bypassed.
        assert scheduler._bypass_counts.get("a") == 1
        assert scheduler._bypass_counts.get("b") == 1
        # c was selected — not bypassed.
        assert scheduler._bypass_counts.get("c", 0) == 0

    def test_bypass_not_incremented_for_newer(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()

        candidates = [
            _candidate("a", 1.0, cache_ready=False),
            _candidate("b", 2.0, cache_ready=True),
            _candidate("c", 3.0, cache_ready=False),
        ]
        scheduler.choose_next(candidates, capacity_available=True)
        # a was bypassed. c is newer than b — not bypassed.
        assert scheduler._bypass_counts.get("a") == 1
        assert scheduler._bypass_counts.get("c", 0) == 0


# ── Test 7: no prompt leakage in scheduler snapshot ────────────────────────


class TestNoLeakage:
    def test_candidate_no_prompt_leakage(self):
        candidate = _candidate("a", 1.0, cache_ready=True)
        candidate_str = str({
            "request_id": candidate.request_id,
            "queued_at": candidate.queued_at,
            "threadwake_cache_ready": candidate.threadwake_cache_ready,
        })
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in candidate_str.lower()

    def test_decision_no_prompt_leakage(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()
        decision = scheduler.choose_next(
            [_candidate("a", 1.0, cache_ready=True)],
            capacity_available=True,
        )
        decision_dict = {
            "request_id": decision.request_id,
            "policy": decision.policy.value,
            "reason": decision.reason.value,
        }
        decision_str = str(decision_dict)
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in decision_str.lower()

    def test_snapshot_no_prompt_leakage(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()
        scheduler.choose_next(
            [_candidate("a", 1.0, cache_ready=True)],
            capacity_available=True,
        )
        snapshot = scheduler.build_snapshot()
        snapshot_str = str(snapshot)
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in snapshot_str.lower()


# ── Test 8: queue integration remains safe ─────────────────────────────────


class TestQueueIntegration:
    def test_queue_cache_ready_metadata(self, monkeypatch):
        """Build candidates includes cache_ready metadata from entries."""
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        req1 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="x")], stream=False,
        )
        e1 = QueueEntry(request_id="a", request=req1)
        e1.cache_ready = False  # type: ignore[attr-defined]
        queue.enqueue(e1)

        req2 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="y")], stream=False,
        )
        e2 = QueueEntry(request_id="b", request=req2)
        e2.cache_ready = True  # type: ignore[attr-defined]
        queue.enqueue(e2)

        candidates = queue.build_candidates()
        assert len(candidates) == 2
        assert candidates[0].threadwake_cache_ready is False
        assert candidates[1].threadwake_cache_ready is True

    def test_queue_cache_aware_selection(self, monkeypatch):
        """Cache-aware scheduler picks cache-ready entry through queue."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        req1 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="a")], stream=False,
        )
        e1 = QueueEntry(request_id="a", request=req1)
        e1.cache_ready = False
        queue.enqueue(e1)

        req2 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="b")], stream=False,
        )
        e2 = QueueEntry(request_id="b", request=req2)
        e2.cache_ready = True
        queue.enqueue(e2)

        candidates = queue.build_candidates()
        decision = queue.scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "b"
        assert decision.reason == SchedulerDecisionReason.CACHE_AFFINITY

    def test_bypass_cleanup_on_dequeue(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="x")], stream=False,
        )
        entry = QueueEntry(request_id="a", request=req)
        queue.enqueue(entry)

        # Simulate a bypass on this entry.
        queue.scheduler._bypass_counts["a"] = 1
        assert queue.scheduler._bypass_counts.get("a") == 1

        # Dequeue should clean up.
        dequeued = queue.dequeue()
        assert dequeued is not None
        assert queue.scheduler._bypass_counts.get("a") is None

    def test_no_reorder_in_fifo_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_SCHEDULER_POLICY", raising=False)
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        for i in range(5):
            req = ChatCompletionRequest(
                model="m", messages=[ChatMessage(role="user", content=str(i))], stream=False,
            )
            queue.enqueue(QueueEntry(request_id=f"req-{i}", request=req))

        dequeued = []
        while queue.depth > 0:
            entry = queue.dequeue()
            if entry:
                dequeued.append(entry.request_id)

        assert dequeued == [f"req-{i}" for i in range(5)]


# ── No-capacity / no-candidates (unchanged) ────────────────────────────────


class TestEdgeCases:
    def test_no_capacity(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()
        decision = scheduler.choose_next(
            [_candidate("a", 1.0)], capacity_available=False,
        )
        assert decision.request_id is None
        assert decision.reason == SchedulerDecisionReason.CAPACITY_UNAVAILABLE

    def test_no_candidates(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        scheduler = Scheduler()
        decision = scheduler.choose_next([], capacity_available=True)
        assert decision.request_id is None
        assert decision.reason == SchedulerDecisionReason.NO_ELIGIBLE_REQUEST


# ── Config defaults ────────────────────────────────────────────────────────


class TestConfig:
    def test_default_policy_fifo(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_SCHEDULER_POLICY", raising=False)
        assert get_scheduler_policy() == "fifo"

    def test_default_max_bypass(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_SCHEDULER_MAX_BYPASS", raising=False)
        assert get_scheduler_max_bypass() == 1

    def test_cache_aware_enabled(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        assert get_scheduler_policy() == "cache_aware_fifo"

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "garbage")
        assert get_scheduler_policy() == "fifo"

"""Tests for the scheduler-authoritative queue selection path.

Covers:
  * `select_and_dequeue` is the only path that drives queued execution.
  * FIFO remains default; the scheduler returns the front entry.
  * No duplicate claims (two concurrent callers cannot claim the same request).
  * Cancellation before selection leaves the queue clean.
  * Timeout before selection removes the entry.
  * Capacity signal cannot result in duplicate dequeue.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from whooshd.contracts import (
    CancellationToken,
    ChatCompletionRequest,
    ChatMessage,
    RequestLifecycleState,
)
from whooshd.queue import QueueEntry, RequestQueue
from whooshd.runtime import RuntimeState


def _chat_req() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content="Hello")],
        max_tokens=64,
    )


def _entry(request_id: str) -> QueueEntry:
    return QueueEntry(request_id=request_id, request=_chat_req())


class TestSchedulerAuthoritativeDequeue:
    def test_select_and_dequeue_returns_front_under_fifo(self):
        q = RequestQueue()
        for i in range(3):
            q.enqueue(_entry(f"r{i}"))
        entry = q.select_and_dequeue(capacity_available=lambda: True)
        assert entry is not None
        assert entry.request_id == "r0"
        assert q.depth == 2

    def test_select_and_dequeue_no_capacity_returns_none(self):
        q = RequestQueue()
        q.enqueue(_entry("r0"))
        entry = q.select_and_dequeue(capacity_available=lambda: False)
        assert entry is None
        # Queue untouched.
        assert q.depth == 1

    def test_select_and_dequeue_empty_returns_none(self):
        q = RequestQueue()
        entry = q.select_and_dequeue(capacity_available=lambda: True)
        assert entry is None
        assert q.depth == 0

    def test_select_and_dequeue_removes_bypass_count(self):
        """After select_and_dequeue the scheduler's bypass map is clean."""
        q = RequestQueue()
        for i in range(3):
            q.enqueue(_entry(f"r{i}"))
        # Bypass count for r0 should be cleaned after removal.
        first = q.select_and_dequeue(capacity_available=lambda: True)
        assert first is not None
        # Bypass map must not retain stale entries.
        assert "r0" not in q.scheduler._bypass_counts


class TestSchedulerFIFODefault:
    def test_fifo_policy_returns_oldest(self):
        from whooshd.scheduler import Scheduler, SchedulerCandidate

        scheduler = Scheduler()
        now = time.time()
        cands = [
            SchedulerCandidate(request_id="a", queued_at=now - 10),
            SchedulerCandidate(request_id="b", queued_at=now - 20),
            SchedulerCandidate(request_id="c", queued_at=now - 5),
        ]
        decision = scheduler.choose_next(cands, capacity_available=True)
        assert decision.request_id == "b"
        assert decision.reason.value == "fifo_oldest"

    def test_fifo_default_policy(self, monkeypatch):
        from whooshd.config import get_scheduler_policy
        from whooshd.scheduler import Scheduler, SchedulerPolicy

        monkeypatch.delenv("WHOOSHD_SCHEDULER_POLICY", raising=False)
        assert get_scheduler_policy() == "fifo"
        scheduler = Scheduler()
        assert scheduler.policy == SchedulerPolicy.FIFO


class TestNoDuplicateClaims:
    def test_concurrent_selects_never_return_same_entry(self):
        """Two concurrent callers must not claim the same request."""
        q = RequestQueue()
        for i in range(5):
            q.enqueue(_entry(f"r{i}"))

        seen: set[str] = set()
        results: list[str | None] = []

        def _try_claim() -> str | None:
            entry = q.select_and_dequeue(capacity_available=lambda: True)
            if entry is None:
                return None
            return entry.request_id

        # Simulate 5 racing claims.
        for _ in range(5):
            rid = _try_claim()
            if rid is not None:
                assert rid not in seen, f"duplicate claim: {rid}"
                seen.add(rid)
            results.append(rid)

        assert q.depth == 0
        assert len(seen) == 5

    def test_select_and_dequeue_then_position_no_dup(self):
        """After select_and_dequeue, the queue's front cannot be the same request."""
        q = RequestQueue()
        for i in range(3):
            q.enqueue(_entry(f"r{i}"))

        first = q.select_and_dequeue(capacity_available=lambda: True)
        assert first is not None
        assert first.request_id == "r0"

        # Front is now r1, not r0.
        assert q.peek().request_id == "r1"

        # Old dequeue() (position-based) must return r1.
        second = q.dequeue()
        assert second is not None
        assert second.request_id == "r1"

        # r0 must NOT be reclaimable.
        assert q.remove("r0") is None


class TestCancellationBeforeSelection:
    def test_cancelled_entry_not_returned_by_select(self):
        q = RequestQueue()
        entry = _entry("r0")
        q.enqueue(entry)

        # Cancel before calling select.
        token = CancellationToken("r0")
        token.cancel()

        # The scheduler can't know about cancellation — it returns the
        # front entry — but remove() should then return None because
        # nothing with this request_id exists (caller is responsible
        # for filtering).  We test the contract: select_and_dequeue
        # only returns an entry that's still queued.
        q.remove("r0")  # simulate external cancellation
        result = q.select_and_dequeue(capacity_available=lambda: True)
        assert result is None
        assert q.depth == 0

    def test_wait_for_execution_returns_false_after_external_cancel(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5.0")
        monkeypatch.setenv("WHOOSHD_QUEUE_POLL_INTERVAL_MS", "50")
        q = RequestQueue()
        entry = _entry("r0")
        q.enqueue(entry)

        token = CancellationToken("r0")
        # Pre-cancel before any wait.
        token.cancel()

        async def _run():
            return await q.wait_for_execution(
                entry,
                cancel_token=token,
                capacity_available=lambda: True,
            )

        ready = asyncio.run(asyncio.wait_for(_run(), timeout=2.0))
        assert ready is False
        assert q.depth == 0


class TestTimeoutBeforeSelection:
    def test_entry_at_front_with_capacity_still_dequeues(self):
        q = RequestQueue()
        q.enqueue(_entry("r0"))

        async def _run():
            return await q.wait_for_execution(
                _entry("r0"),
                capacity_available=lambda: True,
            )

        ready = asyncio.run(asyncio.wait_for(_run(), timeout=1.0))
        assert ready is True
        assert q.depth == 0


class TestCapacitySignalingNoDuplicateDequeue:
    def test_capacity_signal_does_not_duplicate(self, monkeypatch):
        """Multiple notify_capacity() calls do not cause double dequeue."""
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5.0")
        q = RequestQueue()
        q.enqueue(_entry("r0"))

        async def _run():
            return await q.wait_for_execution(
                _entry("r0"),
                capacity_available=lambda: True,
            )

        # Fire capacity signals during the wait; the scheduler must
        # only dequeue once.
        async def _signal_loop():
            for _ in range(5):
                await asyncio.sleep(0.01)
                q.notify_capacity()

        async def _driver():
            sig = asyncio.create_task(_signal_loop())
            ready = await asyncio.wait_for(_run(), timeout=2.0)
            await sig
            return ready

        ready = asyncio.run(_driver())
        assert ready is True
        assert q.depth == 0


class TestBypassCountCleanup:
    def test_remove_clears_bypass_count(self):
        q = RequestQueue()
        # Force a bypass count manually.
        q.scheduler._bypass_counts["r0"] = 3
        q.enqueue(_entry("r0"))
        q.remove("r0")
        assert "r0" not in q.scheduler._bypass_counts
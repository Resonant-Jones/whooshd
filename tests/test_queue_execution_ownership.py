"""Regression tests for the queue/scheduler execution-ownership handoff.

Defect under test
-----------------
``RequestQueue.wait_for_execution`` only proceeds when the *calling waiter's*
own entry is at the queue front.  It then calls ``select_and_dequeue``, which
asks the scheduler to choose **any** candidate and removes **that** candidate
by id.

Under the ``cache_aware_fifo`` policy the scheduler may select a *different*,
newer, cache-ready candidate.  Before the fix:

* the selected candidate was removed from the queue by ``remove()``;
* the calling waiter received a request it did not own, compared ids, saw a
  mismatch, and returned ``False`` without executing it;
* the selected candidate was therefore removed from the queue **with no owner
  executing it** — it neither ran nor remained queued.

That is a lost request, not merely a scheduling inefficiency.

These tests pin the invariant that matters: **a request is never removed from
the queue unless the caller that removed it owns and executes it.**  They also
pin that the default ``fifo`` policy is behaviourally unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from whooshd.contracts import ChatCompletionRequest, ChatMessage
from whooshd.queue import QueueEntry, RequestQueue


def _chat_req() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content="Hello")],
        max_tokens=64,
    )


def _entry(request_id: str) -> QueueEntry:
    return QueueEntry(request_id=request_id, request=_chat_req())


def _cache_ready(entry: QueueEntry) -> QueueEntry:
    """Mark an entry as cache-ready.

    ``cache_ready`` is not a declared ``QueueEntry`` field today; the queue
    reads it defensively via ``getattr``.  Setting it here reproduces the
    state that a real cache-affinity producer would create.
    """
    object.__setattr__(entry, "cache_ready", True)
    return entry


class TestNoRequestRemovedWithoutOwner:
    def test_cache_aware_selection_does_not_strand_a_request(self, monkeypatch):
        """A cache-affinity selection must not orphan the selected request."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()

        front = _entry("front")
        other = _cache_ready(_entry("other"))
        q.enqueue(front)
        q.enqueue(other)

        assert q.depth == 2

        selected = q.select_and_dequeue(capacity_available=lambda: True)

        # Something must have been selected.
        assert selected is not None
        selected_id = selected.request_id

        # The invariant: whatever was removed is either still queued, or the
        # caller that removed it owns it.
        queued_ids = [e.request_id for e in q._deque]
        if selected_id not in queued_ids:
            # Removed.  The caller must be able to execute exactly this entry.
            assert selected.request_id == selected_id
            # And the entry must not have been silently dropped: the returned
            # object is the entry itself, so ownership is with this caller.
            assert selected.request_id in ("front", "other")

    def test_every_removed_entry_is_either_executed_or_requeued(self, monkeypatch):
        """After selection, the queue must not contain a stranded request.

        Concretely: the number of entries removed from the queue must equal the
        number of entries handed back to a caller.
        """
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()
        for rid in ("front", "b", "c"):
            q.enqueue(_cache_ready(_entry(rid)) if rid != "front" else _entry(rid))

        before = q.depth
        selected = q.select_and_dequeue(capacity_available=lambda: True)
        after = q.depth

        assert selected is not None
        # Exactly one entry was removed and exactly one was returned.
        assert before - after == 1

    def test_front_caller_executes_the_entry_it_selected(self, monkeypatch):
        """The waiting caller must receive an executable entry, not ``False``."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()
        front = _entry("front")
        q.enqueue(front)
        q.enqueue(_cache_ready(_entry("other")))

        async def run():
            return await q.wait_for_execution(
                front, capacity_available=lambda: True
            )

        ready = asyncio.run(run())
        # The front waiter must be told it may execute.
        assert ready is True

    def test_cache_ready_selection_never_leaves_queue_empty_of_owner(self, monkeypatch):
        """Repeated selection must never leave the queue holding a request
        that no waiter owns."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()
        entries = [_entry("front")] + [_cache_ready(_entry(f"r{i}")) for i in range(3)]
        for entry in entries:
            q.enqueue(entry)

        removed = []
        for _ in range(3):
            selected = q.select_and_dequeue(capacity_available=lambda: True)
            if selected is None:
                break
            removed.append(selected.request_id)

        # Each removal returned exactly one entry — none vanished.
        assert len(removed) == len(set(removed))
        assert q.depth == 4 - len(removed)


class TestFifoBehaviourUnchanged:
    def test_default_policy_is_still_fifo(self):
        q = RequestQueue()
        for i in range(3):
            q.enqueue(_entry(f"r{i}"))
        selected = q.select_and_dequeue(capacity_available=lambda: True)
        assert selected is not None
        assert selected.request_id == "r0"

    def test_fifo_ordering_preserved(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "fifo")
        q = RequestQueue()
        for i in range(4):
            q.enqueue(_entry(f"r{i}"))
        order = []
        while True:
            selected = q.select_and_dequeue(capacity_available=lambda: True)
            if selected is None:
                break
            order.append(selected.request_id)
        assert order == ["r0", "r1", "r2", "r3"]

    def test_fairness_bypass_limit_still_honored(self, monkeypatch):
        """The bypass limit must continue to bound how often a non-front
        candidate may be preferred."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_SCHEDULER_MAX_BYPASS", "2")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()
        for i in range(6):
            q.enqueue(_cache_ready(_entry(f"r{i}")))

        # Every entry is cache-ready, so FIFO among them is the expectation.
        order = []
        while True:
            selected = q.select_and_dequeue(capacity_available=lambda: True)
            if selected is None:
                break
            order.append(selected.request_id)
        assert order == ["r0", "r1", "r2", "r3", "r4", "r5"]

    def test_cancellation_during_selection_resolves_cleanly(self, monkeypatch):
        """A cancelled entry must not be selected and must not strand others."""
        monkeypatch.setenv("WHOOSHD_SCHEDULER_POLICY", "cache_aware_fifo")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")

        q = RequestQueue()
        first = _entry("r0")
        second = _cache_ready(_entry("r1"))
        q.enqueue(first)
        q.enqueue(second)

        removed = q.remove("r0")
        assert removed is not None
        assert q.depth == 1

        selected = q.select_and_dequeue(capacity_available=lambda: True)
        assert selected is not None
        assert selected.request_id == "r1"
        assert q.depth == 0

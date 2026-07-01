"""Tests for the scheduler policy skeleton."""

from __future__ import annotations

import pytest

from whooshd.scheduler import (
    Scheduler,
    SchedulerCandidate,
    SchedulerDecisionReason,
    SchedulerPolicy,
)


# ── Test 1: FIFO scheduler picks oldest candidate ────────────────────────


class TestFIFOPicksOldest:
    def test_single_candidate_chosen(self):
        scheduler = Scheduler()
        candidates = [
            SchedulerCandidate(request_id="a", queued_at=1.0),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.policy == SchedulerPolicy.FIFO
        assert decision.reason == SchedulerDecisionReason.FIFO_OLDEST
        assert decision.eligible_count == 1

    def test_oldest_of_three_chosen(self):
        scheduler = Scheduler()
        candidates = [
            SchedulerCandidate(request_id="b", queued_at=2.0),
            SchedulerCandidate(request_id="a", queued_at=1.0),
            SchedulerCandidate(request_id="c", queued_at=3.0),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"
        assert decision.eligible_count == 3

    def test_already_ordered_picks_first(self):
        scheduler = Scheduler()
        candidates = [
            SchedulerCandidate(request_id="first", queued_at=1.0),
            SchedulerCandidate(request_id="second", queued_at=2.0),
            SchedulerCandidate(request_id="third", queued_at=3.0),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "first"


# ── Test 2: No capacity returns no request ────────────────────────────────


class TestNoCapacity:
    def test_capacity_unavailable_returns_none(self):
        scheduler = Scheduler()
        candidates = [SchedulerCandidate(request_id="a", queued_at=1.0)]
        decision = scheduler.choose_next(candidates, capacity_available=False)
        assert decision.request_id is None
        assert decision.reason == SchedulerDecisionReason.CAPACITY_UNAVAILABLE
        assert decision.eligible_count == 1

    def test_no_capacity_even_with_many_candidates(self):
        scheduler = Scheduler()
        candidates = [
            SchedulerCandidate(request_id="a", queued_at=1.0),
            SchedulerCandidate(request_id="b", queued_at=2.0),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=False)
        assert decision.request_id is None
        assert decision.eligible_count == 2


# ── Test 3: No candidates returns no request ──────────────────────────────


class TestNoCandidates:
    def test_empty_candidates_returns_none(self):
        scheduler = Scheduler()
        decision = scheduler.choose_next([], capacity_available=True)
        assert decision.request_id is None
        assert decision.reason == SchedulerDecisionReason.NO_ELIGIBLE_REQUEST
        assert decision.eligible_count == 0


# ── Test 4: Scheduler decision does not include prompt content ────────────


class TestDecisionNoLeakage:
    def test_decision_dict_no_prompt_leakage(self):
        scheduler = Scheduler()
        candidates = [
            SchedulerCandidate(request_id="a", queued_at=1.0, model="test-model"),
        ]
        decision = scheduler.choose_next(candidates, capacity_available=True)

        # Serialize and check for forbidden fields.
        decision_dict = {
            "request_id": decision.request_id,
            "policy": decision.policy.value,
            "reason": decision.reason.value,
            "eligible_count": decision.eligible_count,
        }
        decision_str = str(decision_dict)

        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in decision_str.lower(), (
                f"'{forbidden}' leaked in scheduler decision"
            )

    def test_snapshot_no_prompt_leakage(self):
        scheduler = Scheduler()
        scheduler.choose_next(
            [SchedulerCandidate(request_id="a", queued_at=1.0)],
            capacity_available=True,
        )
        snapshot = scheduler.build_snapshot()
        snapshot_str = str(snapshot)

        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in snapshot_str.lower(), (
                f"'{forbidden}' leaked in scheduler snapshot"
            )

    def test_candidate_no_prompt_leakage(self):
        candidate = SchedulerCandidate(
            request_id="a",
            queued_at=1.0,
        )
        candidate_dict = {
            "request_id": candidate.request_id,
            "queued_at": candidate.queued_at,
        }
        candidate_str = str(candidate_dict)
        for forbidden in ("prompt", "message", "content"):
            assert forbidden not in candidate_str.lower()


# ── Test 5: Scheduler integration with queue preserves FIFO ───────────────


class TestQueueIntegration:
    def test_queue_builds_candidates(self):
        """RequestQueue.build_candidates returns safe metadata for each entry."""
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="hello")],
            stream=False,
        )
        entry = QueueEntry(request_id="req-1", request=req)
        queue.enqueue(entry)

        candidates = queue.build_candidates()
        assert len(candidates) == 1
        assert candidates[0].request_id == "req-1"
        assert candidates[0].model == "test-model"
        assert candidates[0].stream is False

    def test_scheduler_chooses_queue_front(self):
        """Scheduler on queue candidates picks the oldest entry."""
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        def _enqueue(rid, t):
            req = ChatCompletionRequest(
                model="m",
                messages=[ChatMessage(role="user", content="x")],
                stream=False,
            )
            entry = QueueEntry(request_id=rid, request=req)
            entry.enqueued_at = t
            queue.enqueue(entry)

        _enqueue("a", 1.0)
        _enqueue("b", 2.0)
        _enqueue("c", 3.0)

        candidates = queue.build_candidates()
        decision = queue.scheduler.choose_next(candidates, capacity_available=True)
        assert decision.request_id == "a"

    def test_no_scheduler_reorder(self):
        """Prove that enqueue order equals dequeue order (no reorder)."""
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()

        for i in range(5):
            req = ChatCompletionRequest(
                model="m",
                messages=[ChatMessage(role="user", content=str(i))],
                stream=False,
            )
            queue.enqueue(QueueEntry(request_id=f"req-{i}", request=req))

        dequeued = []
        while queue.depth > 0:
            entry = queue.dequeue()
            if entry:
                dequeued.append(entry.request_id)

        assert dequeued == [f"req-{i}" for i in range(5)]


# ── Test 6: Scheduler snapshot is safe ────────────────────────────────────


class TestSchedulerSnapshot:
    def test_snapshot_has_expected_shape(self):
        scheduler = Scheduler()
        snapshot = scheduler.build_snapshot()
        assert "policy" in snapshot
        assert snapshot["policy"] == "fifo"
        assert "last_decision_reason" in snapshot
        assert "eligible_count" in snapshot

    def test_snapshot_updates_after_decision(self):
        scheduler = Scheduler()
        scheduler.choose_next(
            [SchedulerCandidate(request_id="a", queued_at=1.0)],
            capacity_available=True,
        )
        snapshot = scheduler.build_snapshot()
        assert snapshot["last_decision_reason"] == "fifo_oldest"
        assert snapshot["eligible_count"] == 1


# ── Test 7: Default policy is FIFO ────────────────────────────────────────


class TestDefaultPolicy:
    def test_default_policy_is_fifo(self):
        scheduler = Scheduler()
        assert scheduler.policy == SchedulerPolicy.FIFO

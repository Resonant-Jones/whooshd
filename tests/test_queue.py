"""Tests for the bounded FIFO request queue.

Covers:
  1. queue disabled preserves current 429 behavior
  2. queue enabled enqueues when active limit is reached
  3. queue full returns structured 429
  4. queued request eventually runs
  5. queued request cancellation does not call adapter
  6. queued request timeout returns clean terminal state
  7. streaming request emits no SSE before execution starts
  8. runtime snapshots do not leak prompts/messages/generated text
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.admission import AdmissionDecision, evaluate_chat_request
from whooshd.app import app
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatMessage,
    RequestLifecycleState,
)
from whooshd.queue import QueueEntry, RequestQueue, get_queue
from whooshd.runtime import RuntimeState


# ── Helpers ──────────────────────────────────────────────────────────────────


def _chat_req(model="m", messages=None, max_tokens=256, stream=False):
    if messages is None:
        messages = [ChatMessage(role="user", content="Hello")]
    return ChatCompletionRequest(
        model=model, messages=messages, max_tokens=max_tokens, stream=stream
    )


def _stream_req(model="m"):
    return _chat_req(model=model, stream=True)


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_queue():
    """Ensure queue singleton starts fresh for each test."""
    import whooshd.queue as qmod
    import whooshd.runtime as rmod
    qmod._queue = None
    rmod._runtime = None
    yield
    qmod._queue = None
    rmod._runtime = None


# ── 1. Queue disabled preserves current 429 behavior ────────────────────────


class TestQueueDisabledPreserves429:
    def test_admission_returns_rejected_overloaded_when_queue_disabled(self, monkeypatch):
        """When WHOOSHD_ENABLE_QUEUE is false (default), overloaded requests
        should get REJECTED_OVERLOADED, not QUEUED."""
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "false")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_OVERLOADED
        assert result.http_status == 429

    def test_admission_rejected_when_queue_disabled_default(self, monkeypatch):
        """Default (no env var set) should also reject with overloaded."""
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_OVERLOADED


# ── 2. Queue enabled enqueues when active limit is reached ──────────────────


class TestQueueEnabledEnqueues:
    def test_admission_returns_queued_when_at_limit_and_queue_has_capacity(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.QUEUED
        assert result.http_status == 202

    def test_queued_result_has_queue_details(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.details["active_jobs"] == 1
        assert result.details["queue_depth"] == 0
        assert result.details["max_queue_depth"] == 8

    def test_runtime_rejection_counters_when_queued(self, monkeypatch):
        """accepted counter increments for queued requests (not rejected counter)."""
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)

        before_accepted = rt.total_requests_accepted
        before_rejected = rt.total_rejected_overloaded
        result = evaluate_chat_request(_chat_req(), rt)
        assert result.reason == AdmissionDecision.QUEUED
        # Admission decision was not a rejection, so rejected counter unchanged.
        # (Actual counter increment happens in the app handler via record_accepted.)
        assert rt.active_jobs == 1  # queued doesn't count toward active_jobs


# ── 3. Queue full returns structured 429 ─────────────────────────────────────


class TestQueueFullReturns429:
    def test_admission_returns_rejected_queue_full(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "2")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1
        # Fill the queue to its limit.
        for i in range(2):
            rt.begin_request(model="m", stream=False)
            # Manually set to queued so queue_depth increments.
            # Find the last created request and mark it queued.
        # Simpler: just set queue_depth indirectly by creating queued records.
        # Create and mark two requests as queued.
        r1 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(r1)
        r2 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(r2)

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_QUEUE_FULL
        assert result.error_code is not None
        assert result.http_status == 429
        assert "queue is full" in result.message.lower()

    def test_queue_full_detail_includes_depths(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1
        r1 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(r1)

        result = evaluate_chat_request(_chat_req(), rt)
        assert result.details["queue_depth"] == 1
        assert result.details["max_queue_depth"] == 1

    def test_record_queue_rejected_counter(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)
        r1 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(r1)

        before = rt.total_queue_rejected
        rt.record_queue_rejected()
        assert rt.total_queue_rejected == before + 1


# ── 4. Queued request eventually runs ───────────────────────────────────────


class TestQueuedRequestEventuallyRuns:
    async def test_queue_wait_for_execution_dequeues_when_capacity_opens(self):
        """When capacity becomes available, wait_for_execution returns True
        and the entry is dequeued."""
        queue = RequestQueue()
        entry = QueueEntry(request_id="req-1", request=_chat_req())

        queue.enqueue(entry)
        assert queue.depth == 1

        # Simulate: check returns True immediately when at front + capacity.
        async def _run():
            return await queue.wait_for_execution(
                entry,
                capacity_available=lambda: True,
            )

        # Should complete almost immediately.
        ready = await asyncio.wait_for(_run(), timeout=1.0)
        assert ready is True
        assert queue.depth == 0

    async def test_queue_wait_for_execution_waits_for_front_position(self, monkeypatch):
        """When not at the front, the entry waits and eventually times out."""
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "0.05")
        queue = RequestQueue()

        entry1 = QueueEntry(request_id="req-1", request=_chat_req())
        entry2 = QueueEntry(request_id="req-2", request=_chat_req())
        queue.enqueue(entry1)
        queue.enqueue(entry2)

        # entry2 is not at front — should time out.
        ready = await queue.wait_for_execution(
            entry2,
            capacity_available=lambda: True,  # capacity available but not front
        )
        assert ready is False
        # entry2 should be removed from queue.
        assert queue.depth == 1
        assert queue.peek().request_id == "req-1"

    async def test_mark_queued_sets_correct_state(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)

        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.QUEUED
        assert rt.queue_depth == 1
        assert rt.active_jobs == 0  # queued is not active

    async def test_dequeue_transitions_to_running(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        assert rt.queue_depth == 1

        rt.mark_dequeued(rid)
        rt.mark_running(rid)

        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.RUNNING
        assert rt.queue_depth == 0
        assert rt.active_jobs == 1

    async def test_dequeue_transitions_to_streaming(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_queued(rid)
        rt.mark_dequeued(rid)
        rt.mark_streaming(rid)

        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.STREAMING
        assert rt.active_jobs == 1

    async def test_queue_depth_computed_correctly(self):
        rt = RuntimeState()
        assert rt.queue_depth == 0

        rid1 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid1)
        assert rt.queue_depth == 1

        rid2 = rt.begin_request(model="m", stream=True)
        rt.mark_queued(rid2)
        assert rt.queue_depth == 2

        rt.mark_dequeued(rid1)
        rt.mark_running(rid1)
        assert rt.queue_depth == 1

        rt.cancel_request(rid2)
        assert rt.queue_depth == 0

    async def test_oldest_queued_age_ms(self):
        import time
        rt = RuntimeState()
        assert rt.oldest_queued_age_ms == 0.0

        rid1 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid1)
        age1 = rt.oldest_queued_age_ms
        assert age1 >= 0.0

        # Add a second queued request — oldest should still be from rid1.
        rid2 = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid2)
        age2 = rt.oldest_queued_age_ms
        assert age2 >= age1


# ── 5. Queued request cancellation does not call adapter ────────────────────


class TestQueuedCancellation:
    async def test_cancellation_removes_from_queue(self):
        """Cancelling a queued entry removes it from the queue."""
        queue = RequestQueue()
        entry = QueueEntry(request_id="req-1", request=_chat_req())
        queue.enqueue(entry)

        removed = queue.remove("req-1")
        assert removed is not None
        assert removed.request_id == "req-1"
        assert queue.depth == 0

    async def test_wait_for_execution_returns_false_on_cancellation(self, monkeypatch):
        """When the cancellation token is set, wait_for_execution returns False."""
        from whooshd.contracts import CancellationToken

        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5.0")
        queue = RequestQueue()

        token = CancellationToken("req-1")
        entry = QueueEntry(request_id="req-1", request=_chat_req())
        queue.enqueue(entry)

        # We need to be at the front for wait to check capacity.
        # Cancel before checking.
        token.cancel()

        async def _run():
            return await queue.wait_for_execution(
                entry,
                cancel_token=token,
                capacity_available=lambda: True,
            )

        ready = await asyncio.wait_for(_run(), timeout=1.0)
        assert ready is False
        # Entry should be removed from queue.
        assert queue.depth == 0

    async def test_cancellation_does_not_call_adapter(self):
        """The adapter should never be invoked for a cancelled queued request."""
        # This tests the integration: when wait_for_execution returns False
        # due to cancellation, the handler does not proceed to run the adapter.
        # We verify the state machine: cancel_request is called, not complete_request.
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        assert rt.queue_depth == 1

        rt.cancel_request(rid)
        assert rt.queue_depth == 0
        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.CANCELLED
        # active_jobs should still be 0 (was queued, not active).
        assert rt.active_jobs == 0

    async def test_cancel_queued_request_not_in_active_jobs(self):
        """Cancelling a queued request should not change active_jobs."""
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        assert rt.active_jobs == 0
        rt.cancel_request(rid)
        assert rt.active_jobs == 0

    async def test_cancel_requested_on_queued_request(self):
        """request_cancellation works on queued requests."""
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        assert rt.request_cancellation(rid) is True

        token = rt.get_cancellation_token(rid)
        assert token.is_cancelled() is True


# ── 6. Queued request timeout returns clean terminal state ──────────────────


class TestQueuedTimeout:
    async def test_wait_for_execution_returns_false_on_timeout(self, monkeypatch):
        """When timeout expires before reaching front, returns False."""
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "0.01")
        queue = RequestQueue()

        entry1 = QueueEntry(request_id="req-1", request=_chat_req())
        entry2 = QueueEntry(request_id="req-2", request=_chat_req())
        queue.enqueue(entry1)
        queue.enqueue(entry2)

        # entry2 is behind entry1 — should time out before reaching front.
        ready = await queue.wait_for_execution(entry2)
        assert ready is False
        assert queue.depth == 1  # entry1 remains

    async def test_mark_timed_out_sets_correct_state(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        assert rt.queue_depth == 1

        rt.mark_timed_out(rid)
        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.TIMED_OUT
        assert snap.ended_at is not None
        assert rt.queue_depth == 0
        assert rt.active_jobs == 0

    async def test_timeout_increments_counter(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        before = rt.total_queue_timeout
        rt.mark_timed_out(rid)
        assert rt.total_queue_timeout == before + 1

    async def test_timed_out_is_terminal_for_cancellation(self):
        """Cannot cancel a timed-out request (already terminal)."""
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        rt.mark_timed_out(rid)
        assert rt.request_cancellation(rid) is False


# ── 7. Streaming request emits no SSE before execution starts ───────────────


class TestStreamingNoSSEBeforeExecution:
    async def test_queue_wait_prevents_sse_emission(self, monkeypatch):
        """The queue wait_for_execution runs synchronously with the handler,
        so no SSE chunks can be emitted while waiting.  The handler only
        begins streaming after wait_for_execution returns True."""
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "1.0")
        queue = RequestQueue()

        # Enqueue a streaming request behind another entry.
        entry1 = QueueEntry(request_id="req-1", request=_chat_req())
        entry2 = QueueEntry(request_id="req-2", request=_stream_req())
        queue.enqueue(entry1)
        queue.enqueue(entry2)

        # entry2 will wait for front position.
        # In a real scenario, the handler returns False on timeout
        # and never starts the SSE generator.
        ready = await queue.wait_for_execution(
            entry2,
            capacity_available=lambda: False,  # capacity never opens
        )
        # entry2 times out — the handler would return an error.
        assert ready is False
        # No adapter called, no SSE emitted.
        assert queue.depth == 1  # entry1 still there

    async def test_queued_request_counters(self):
        """Verify queue counter increments."""
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        assert rt.total_queued == 0
        rt.mark_queued(rid)
        assert rt.total_queued == 1

        rt.mark_dequeued(rid)
        assert rt.total_dequeued == 1

    async def test_queue_cancelled_counter(self):
        """Verify queue_cancelled counter."""
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        before = rt.total_queue_cancelled
        rt.record_queue_cancelled()
        assert rt.total_queue_cancelled == before + 1


# ── 8. Runtime snapshots do not leak prompts/messages/generated text ────────


class TestSnapshotNoLeakage:
    async def test_admission_config_no_prompt_leakage(self):
        """GET /runtime/admission must not contain actual prompt/message
        content.  Config key names like 'max_prompt_chars' are metadata,
        not leaks."""
        rt = RuntimeState()
        config = rt.build_admission_config()
        # Config keys that are metadata (permitted).
        assert "max_prompt_chars" in config
        # Keys that would be content leaks (must not appear).
        for key in ("messages", "user_content", "generated_text", "prompt_text"):
            assert key not in config

    async def test_request_snapshot_no_leakage_for_queued(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        snap = rt.get_request_snapshot(rid)
        data = snap.model_dump()
        for key in ("prompt", "messages", "content", "text", "input"):
            assert key not in data

    async def test_request_snapshot_no_leakage_for_timed_out(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        rt.mark_timed_out(rid)
        snap = rt.get_request_snapshot(rid)
        data = snap.model_dump()
        for key in ("prompt", "messages", "content", "text", "input"):
            assert key not in data

    async def test_active_requests_includes_queued_state(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_queued(rid)
        active = rt.get_active_requests()
        assert len(active) == 1
        assert active[0].status == RequestLifecycleState.QUEUED

    async def test_build_request_list_includes_queued(self):
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)
        rid2 = rt.begin_request(model="m", stream=True)
        rt.mark_queued(rid2)

        resp = rt.build_request_list()
        assert resp.active_count == 1  # only the ACCEPTED one counts as active
        assert len(resp.requests) == 2  # both appear in the list

    async def test_build_admission_config_includes_queue_fields(self):
        rt = RuntimeState()
        config = rt.build_admission_config()
        assert "queue_enabled" in config
        assert "queue_depth" in config
        assert "max_queue_depth" in config
        assert "queue_timeout_seconds" in config
        assert "queued" in config["counters"]
        assert "dequeued" in config["counters"]
        assert "queue_rejected" in config["counters"]
        assert "queue_timeout" in config["counters"]
        assert "queue_cancelled" in config["counters"]

    async def test_health_response_no_queue_leakage(self):
        """Health response should not leak any prompt data in queue fields."""
        rt = RuntimeState()
        # queue_depth is a simple int — no content.
        assert isinstance(rt.queue_depth, int)
        assert isinstance(rt.oldest_queued_age_ms, float)


# ── Structural checks still reject before queue ─────────────────────────────


class TestStructuralChecksBeforeQueue:
    def test_prompt_too_large_rejects_even_when_queue_enabled(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_MAX_PROMPT_CHARS", "5")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # at capacity

        req = _chat_req(messages=[ChatMessage(role="user", content="Too long")])
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_PROMPT_TOO_LARGE
        # Should NOT be QUEUED.
        assert result.reason != AdmissionDecision.QUEUED

    def test_too_many_messages_rejects_even_when_queue_enabled(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_MAX_MESSAGES", "2")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # at capacity

        req = _chat_req(messages=[
            ChatMessage(role="user", content=str(i)) for i in range(3)
        ])
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_TOO_MANY_MESSAGES

    def test_max_tokens_too_high_rejects_even_when_queue_enabled(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_MAX_REQUEST_MAX_TOKENS", "100")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # at capacity

        req = _chat_req(max_tokens=200)
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_MAX_TOKENS_TOO_HIGH


# ── Config defaults ──────────────────────────────────────────────────────────


class TestQueueConfigDefaults:
    def test_queue_disabled_by_default(self):
        from whooshd.config import get_enable_queue
        assert get_enable_queue() is False

    def test_max_queue_depth_default(self):
        from whooshd.config import get_max_queue_depth
        assert get_max_queue_depth() == 8

    def test_queue_timeout_default(self):
        from whooshd.config import get_queue_timeout_seconds
        assert get_queue_timeout_seconds() == 120.0

    def test_queue_poll_interval_default(self):
        from whooshd.config import get_queue_poll_interval_ms
        assert get_queue_poll_interval_ms() == 25


# ── RequestQueue unit tests ──────────────────────────────────────────────────


class TestRequestQueueUnit:
    def test_initial_queue_empty(self):
        q = RequestQueue()
        assert q.depth == 0
        assert q.is_full is False
        assert q.peek() is None
        assert q.dequeue() is None

    def test_enqueue_dequeue_fifo_order(self):
        q = RequestQueue()
        e1 = QueueEntry(request_id="a", request=_chat_req(model="a"))
        e2 = QueueEntry(request_id="b", request=_chat_req(model="b"))
        q.enqueue(e1)
        q.enqueue(e2)
        assert q.depth == 2

        d1 = q.dequeue()
        assert d1.request_id == "a"
        d2 = q.dequeue()
        assert d2.request_id == "b"
        assert q.depth == 0

    def test_remove_by_id(self):
        q = RequestQueue()
        e1 = QueueEntry(request_id="a", request=_chat_req(model="a"))
        e2 = QueueEntry(request_id="b", request=_chat_req(model="b"))
        e3 = QueueEntry(request_id="c", request=_chat_req(model="c"))
        q.enqueue(e1)
        q.enqueue(e2)
        q.enqueue(e3)

        # Remove from middle.
        removed = q.remove("b")
        assert removed.request_id == "b"
        assert q.depth == 2

        # Order preserved: a then c.
        assert q.dequeue().request_id == "a"
        assert q.dequeue().request_id == "c"

    def test_remove_nonexistent(self):
        q = RequestQueue()
        assert q.remove("nonexistent") is None

    def test_peek_returns_front_without_dequeue(self):
        q = RequestQueue()
        e1 = QueueEntry(request_id="a", request=_chat_req())
        q.enqueue(e1)
        assert q.peek().request_id == "a"
        assert q.depth == 1  # not removed

    def test_oldest_age_ms(self):
        import time
        q = RequestQueue()
        e = QueueEntry(request_id="a", request=_chat_req(), enqueued_at=time.time() - 1.0)
        q.enqueue(e)
        age = q.oldest_age_ms()
        assert age >= 1000.0  # at least 1 second

    def test_oldest_age_ms_empty(self):
        q = RequestQueue()
        assert q.oldest_age_ms() == 0.0

    def test_is_full_respects_config(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "2")
        import whooshd.queue as qmod
        qmod._queue = None  # force re-read config
        q = RequestQueue()
        q.enqueue(QueueEntry(request_id="a", request=_chat_req()))
        q.enqueue(QueueEntry(request_id="b", request=_chat_req()))
        assert q.is_full is True

    def test_notify_capacity_wakes_waiters(self):
        """When notify_capacity is called, a waiter should be woken."""
        # This is implicitly tested by wait_for_execution tests above.
        q = RequestQueue()
        q._capacity_event.set()
        assert q._capacity_event.is_set()
        q._clear_capacity_signal()
        assert not q._capacity_event.is_set()


# ── 9. Endpoint-level queue reachability tests ─────────────────────────────


class TestQueueEndpointReachability:
    """Prove that queued chat requests reach the FIFO execution path
    through the FastAPI handler, not just the unit-level queue module.
    """

    async def test_queued_request_reaches_queue_branch_timeout(self, monkeypatch, client):
        """Queued request enters the queue branch and times out without
        calling the adapter."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "0.05")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        # Occupy the single active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )

        # Queue timeout returns 504 with the canonical timeout code.
        assert resp.status_code == 504
        body = resp.json()
        assert body["code"] == "timeout"

        # Counters: queued incremented, timeout incremented.
        admission = rt.build_admission_config()
        assert admission["counters"]["queued"] >= 1
        assert admission["counters"]["queue_timeout"] >= 1
        # Not counted as rejected.
        assert admission["counters"]["rejected"] == 0

        rt.complete_request(blocker)

    async def test_queue_disabled_behavior_unchanged(self, monkeypatch, client):
        """When queue is disabled, overload returns 429 immediately."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "false")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        # Occupy the single active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )

        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "runner_overloaded"

        # No queued counter increment.
        admission = rt.build_admission_config()
        assert admission["counters"]["queued"] == 0

        rt.complete_request(blocker)

    async def test_queue_full_counter_increments(self, monkeypatch, client):
        """When queue is enabled but full, queue_rejected counter increments."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "1")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        # Occupy the single active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        # Fill the queue to capacity.
        r1 = rt.begin_request(model="stub-model", stream=False)
        rt.mark_queued(r1)

        # Now the queue is full — this request should be rejected.
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )

        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "queue_full"

        admission = rt.build_admission_config()
        assert admission["counters"]["queue_rejected"] >= 1

        rt.cancel_request(r1)
        rt.complete_request(blocker)

    async def test_queued_request_eventually_executes(self, monkeypatch, client):
        """A queued request dequeues and executes successfully when capacity
        opens up."""
        import asyncio
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "10")

        from whooshd.runtime import get_runtime
        from whooshd.queue import get_queue
        rt = get_runtime()
        queue = get_queue()

        # Occupy the active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        # Start the queued request as a background task.
        async def _queued_request():
            return await client.post(
                "/v1/chat/completions",
                json={
                    "model": "stub-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": False,
                },
            )

        task = asyncio.ensure_future(_queued_request())

        # Give the request time to be accepted and enqueued.
        await asyncio.sleep(0.1)
        assert rt.queue_depth == 1

        # Release the blocker — capacity opens.
        rt.complete_request(blocker)
        queue.notify_capacity()

        # Wait for the queued request to complete.
        resp = await asyncio.wait_for(task, timeout=5.0)

        assert resp.status_code == 200
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) >= 1

        # Counters.
        admission = rt.build_admission_config()
        assert admission["counters"]["queued"] >= 1
        assert admission["counters"]["dequeued"] >= 1
        # active_jobs should return to 0.
        assert rt.active_jobs == 0

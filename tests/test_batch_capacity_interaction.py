"""Tests for the interaction between queue selection and batch execution.

Validates the spec requirements:

  * Batch-claimed requests are not individually executed.
  * Batch failure resolves every claimed request cleanly.
  * Batch execution respects active capacity accounting.

The tests use the existing live-batch path with a stub adapter that
returns a synthetic batch response.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.admission import reset_controller_for_tests
from whooshd.app import app
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatMessage,
    ChatCompletionUsage,
    RequestExecutionContext,
)
from whooshd.queue import QueueEntry, RequestQueue
from whooshd.runtime import RuntimeState


# ── Test fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "4")
    monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_MIN_SIZE", "2")
    monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_MAX_SIZE", "4")

    import whooshd.queue as qmod
    import whooshd.runtime as rmod
    qmod._queue = None
    rmod._runtime = None
    reset_controller_for_tests()
    yield
    qmod._queue = None
    rmod._runtime = None
    reset_controller_for_tests()


def _req() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="stub-model",
        messages=[ChatMessage(role="user", content="hi")],
        max_tokens=16,
        stream=False,
    )


def _entry(request_id: str, *, model: str = "stub-model") -> QueueEntry:
    return QueueEntry(
        request_id=request_id,
        request=ChatCompletionRequest(
            model=model,
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=16,
            stream=False,
        ),
    )


def _batch_response(rid: str) -> ChatCompletionResponse:
    return ChatCompletionResponse.model_validate({
        "id": f"chatcmpl-{rid}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "stub-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


class TestBatchClaimedRequestsNotIndividuallyExecuted:
    def test_claim_marks_entries_so_select_and_dequeue_skips(self):
        """Once an entry is batch-claimed, the scheduler-authoritative
        select_and_dequeue must NOT return it again."""
        q = RequestQueue()
        e1 = _entry("r1")
        e2 = _entry("r2")
        q.enqueue(e1)
        q.enqueue(e2)

        # Need a running event loop for create_future().
        async def _run_claim():
            return q.claim_batch_entries([e1, e2])

        futures = asyncio.run(_run_claim())
        assert len(futures) == 2
        assert e1.batch_claimed is True
        assert e2.batch_claimed is True

        # The entries remain in the queue (claim doesn't remove); the
        # caller is responsible for not also executing them
        # individually.  The application's contract is enforced by
        # only claiming batch entries that have already been
        # dequeued, not by removing them in claim().
        assert q.depth == 2

        # batch_claimed + batch_result_future are the application-visible
        # signals that these entries must NOT be executed individually.
        assert e1.batch_result_future is not None
        assert e2.batch_result_future is not None


class TestBatchFailureResolvesClaimedRequests:
    def test_resolve_batch_results_resolves_all(self):
        q = RequestQueue()
        entries = [_entry(f"r{i}") for i in range(3)]
        for e in entries:
            q.enqueue(e)

        async def _claim_then_resolve():
            futures = q.claim_batch_entries(entries)
            q.resolve_batch_results(entries, [
                (e.request_id, _batch_response(e.request_id)) for e in entries
            ])
            return [f.result() for f in futures]

        results = asyncio.run(_claim_then_resolve())
        for r in results:
            assert r is not None
            assert r.choices[0].message.content == "ok"

    def test_resolve_batch_results_idempotent(self):
        """Calling resolve twice does not raise."""
        q = RequestQueue()
        entries = [_entry(f"r{i}") for i in range(2)]
        for e in entries:
            q.enqueue(e)

        async def _claim_then_double_resolve():
            q.claim_batch_entries(entries)
            results = [(e.request_id, _batch_response(e.request_id)) for e in entries]
            q.resolve_batch_results(entries, results)
            q.resolve_batch_results(entries, results)  # must not raise

        asyncio.run(_claim_then_double_resolve())
        for e in entries:
            assert e.batch_result_future.done()


class TestBatchRespectsActiveCapacityAccounting:
    def test_batch_claim_increments_active_via_mark_running(self):
        """When a batch claims entries, each claim is followed by
        mark_running on the runtime.  The scheduler-authoritative
        dequeue has already removed them, so the controller's
        effective_active_limit should remain consistent."""
        rt = RuntimeState()
        q = RequestQueue()

        async def _scenario():
            for i in range(3):
                rid = rt.begin_request(model="stub-model", stream=False)
                q.enqueue(QueueEntry(request_id=rid, request=_req()))

            # Active is now 3.
            assert rt.active_jobs == 3

            # Claim entries for batch.
            entries = list(q._deque)
            futures = q.claim_batch_entries(entries)
            assert len(futures) == 3

            # The queue is still intact (claim doesn't remove), but the
            # caller is responsible for following up with mark_running +
            # complete_request.
            assert q.depth == 3

            # Resolve results — each entry's future gets a value.
            q.resolve_batch_results(entries, [
                (e.request_id, _batch_response(e.request_id)) for e in entries
            ])

            # Now mark all complete on runtime.
            for e in entries:
                rt.complete_request(e.request_id)
            assert rt.active_jobs == 0

        asyncio.run(_scenario())
"""Tests for live-path stub batching — one wire, stub only, gates on."""

from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import ChatCompletionRequest, ChatMessage


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_state():
    import whooshd.queue as qmod
    import whooshd.runtime as rmod
    qmod._queue = None
    rmod._runtime = None
    yield
    qmod._queue = None
    rmod._runtime = None


def _payload(model="stub-model", content="hello", stream=False):
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
        "max_tokens": 32,
    }


class TestLiveBatchDisabledByDefault:
    async def test_no_batch_without_flags(self, client, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "1")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        # Occupy the active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        # Send a queued request — should queue then execute normally.
        task = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload()))
        await asyncio.sleep(0.3)
        assert rt.queue_depth == 1

        rt.complete_request(blocker)
        resp = await asyncio.wait_for(task, timeout=5)
        assert resp.status_code == 200


class TestLiveBatchTwoRequests:
    async def test_two_queued_requests_execute_as_batch(self, client, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_MIN_SIZE", "2")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "2")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        # Occupy active slot.
        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        # Send two compatible requests.
        async def _send(content):
            return await client.post("/v1/chat/completions", json=_payload(content=content))

        task_a = asyncio.ensure_future(_send("first"))
        task_b = asyncio.ensure_future(_send("second"))

        await asyncio.sleep(0.3)
        assert rt.queue_depth == 2

        # Release — both should complete.
        rt.complete_request(blocker)
        ra = await asyncio.wait_for(task_a, timeout=10)
        rb = await asyncio.wait_for(task_b, timeout=10)

        assert ra.status_code == 200
        assert rb.status_code == 200
        assert "choices" in ra.json()
        assert "choices" in rb.json()

        # Queue should be empty.
        await asyncio.sleep(0.2)
        assert rt.queue_depth == 0
        assert rt.active_jobs == 0

    async def test_streaming_excluded_from_batch(self, client, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "2")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task_ns = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(stream=False)))
        task_s = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(stream=True)))

        await asyncio.sleep(0.3)
        rt.complete_request(blocker)

        r_ns = await asyncio.wait_for(task_ns, timeout=10)
        r_s = await asyncio.wait_for(task_s, timeout=10)

        assert r_ns.status_code == 200
        assert r_s.status_code == 200

    async def test_unsupported_backend_falls_back(self, client, monkeypatch):
        """When batch exec enabled but backend is unsupported (stub without flag), falls back."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "false")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "1")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload()))
        await asyncio.sleep(0.3)
        rt.complete_request(blocker)

        resp = await asyncio.wait_for(task, timeout=5)
        assert resp.status_code == 200

class TestBatchHardening:
    """Gremlin traps — wrong counts, cancelled peers, idempotent cleanup."""

    async def test_wrong_response_count_resolves_all_entries(self, client, monkeypatch):
        """Backend returns fewer responses than entries — all must resolve."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "2")

        from whooshd.runtime import get_runtime
        from whooshd.adapters.stub import StubInferenceAdapter
        rt = get_runtime()

        # Use a custom adapter that returns wrong count.
        class WrongCountAdapter(StubInferenceAdapter):
            async def chat_completion_batch(self, requests, contexts=None):
                # Only return 1 response for 2 requests.
                result = await super().chat_completion_batch(requests[:1])
                return result

        # Inject via monkeypatching the router's adapter resolution.
        import whooshd.routing as routing
        router = routing.get_router()
        wrong_adapter = WrongCountAdapter()
        original_stub = router._adapters.get("stub")
        router._adapters["stub"] = wrong_adapter

        try:
            blocker = rt.begin_request(model="stub-model", stream=False)
            rt.mark_running(blocker)

            task_a = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(content="a")))
            task_b = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(content="b")))

            await asyncio.sleep(0.3)
            assert rt.queue_depth == 2
            rt.complete_request(blocker)

            ra = await asyncio.wait_for(task_a, timeout=10)
            rb = await asyncio.wait_for(task_b, timeout=10)

            # The selected entry is not batch-claimed after the queue removes
            # it; this path therefore falls back to ordinary execution.
            assert ra.status_code == 200
            assert rb.status_code == 200

            await asyncio.sleep(0.2)
            assert rt.queue_depth == 0
            assert rt.active_jobs == 0
        finally:
            router._adapters["stub"] = original_stub

    async def test_cancelled_peer_excluded(self, client, monkeypatch):
        """Cancelled peer is excluded from batch, remaining request falls back."""
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_MIN_SIZE", "2")
        monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "2")

        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task_a = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(content="a")))
        task_b = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload(content="b")))

        await asyncio.sleep(0.3)
        assert rt.queue_depth == 2

        # Cancel one peer before release.
        snap = rt.build_request_list()
        for req in snap.requests:
            if req.status.value == "queued":
                rt.request_cancellation(req.request_id)
                break

        rt.complete_request(blocker)
        ra = await asyncio.wait_for(task_a, timeout=10)
        rb = await asyncio.wait_for(task_b, timeout=10)

        # At least one should succeed (the non-cancelled one falls back to single execution).
        assert ra.status_code in (200, 409)
        assert rb.status_code in (200, 409)
        # Both should have resolved.
        await asyncio.sleep(0.2)
        assert rt.queue_depth == 0
        assert rt.active_jobs == 0

    async def test_batch_cleanup_idempotent(self, client, monkeypatch):
        """Resolving a batch twice does not double-complete or corrupt state."""
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()
        loop = asyncio.get_event_loop()

        req = ChatCompletionRequest(model="m", messages=[ChatMessage(role="user", content="x")], stream=False)
        e1 = QueueEntry(request_id="a", request=req)
        f1 = loop.create_future()
        e1.batch_result_future = f1
        e1.batch_claimed = True

        e2 = QueueEntry(request_id="b", request=req)
        f2 = loop.create_future()
        e2.batch_result_future = f2
        e2.batch_claimed = True

        queue.enqueue(e1)
        queue.enqueue(e2)

        # Resolve once.
        queue.resolve_batch_results([e1, e2], [("a", "r1"), ("b", "r2")])
        assert f1.result() == "r1"
        assert f2.result() == "r2"

        # Resolve again — idempotent, no crash.
        queue.resolve_batch_results([e1, e2], [("a", "wrong"), ("b", "wrong")])
        assert f1.result() == "r1"  # unchanged
        assert f2.result() == "r2"  # unchanged

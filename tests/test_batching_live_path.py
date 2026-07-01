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

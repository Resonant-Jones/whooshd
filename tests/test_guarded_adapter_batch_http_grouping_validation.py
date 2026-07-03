"""Tests for guarded adapter-batch HTTP queue/admission grouping validation."""

import asyncio, json, pytest
from httpx import ASGITransport, AsyncClient
from whooshd.app import app
from whooshd.contracts import ChatCompletionRequest, ChatMessage


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    import whooshd.queue as qmod
    import whooshd.runtime as rmod
    qmod._queue = None
    rmod._runtime = None
    monkeypatch.setenv("WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "2")
    monkeypatch.setenv("WHOOSHD_GUARDED_ADAPTER_BATCHING_MIN_GROUP_SIZE", "2")
    monkeypatch.setenv("WHOOSHD_GUARDED_ADAPTER_BATCHING_MAX_GROUP_SIZE", "2")
    monkeypatch.setenv("WHOOSHD_GUARDED_ADAPTER_BATCHING_MAX_TOKENS", "128")
    yield
    qmod._queue = None
    rmod._runtime = None


def _payload(content="hello"):
    return {"model": "stub-model", "messages": [{"role": "user", "content": content}], "stream": False, "max_tokens": 32}


class TestHTTPGrouping:
    async def test_two_requests_complete(self, client):
        """Two compatible HTTP requests complete through the queue/admission path."""
        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task_a = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("first")))
        task_b = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("second")))

        await asyncio.sleep(0.3)
        assert rt.queue_depth == 2

        rt.complete_request(blocker)

        ra = await asyncio.wait_for(task_a, timeout=10)
        rb = await asyncio.wait_for(task_b, timeout=10)

        assert ra.status_code == 200
        assert rb.status_code == 200

        await asyncio.sleep(0.2)
        assert rt.queue_depth == 0
        assert rt.active_jobs == 0

    async def test_responses_openai_compatible(self, client):
        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task_a = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("a")))
        task_b = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("b")))

        await asyncio.sleep(0.3)
        rt.complete_request(blocker)

        ra = await asyncio.wait_for(task_a, timeout=10)
        rb = await asyncio.wait_for(task_b, timeout=10)

        for r in (ra, rb):
            body = r.json()
            assert "choices" in body
            assert body["choices"][0]["message"]["content"]

    async def test_no_metadata_in_response(self, client):
        from whooshd.runtime import get_runtime
        rt = get_runtime()

        blocker = rt.begin_request(model="stub-model", stream=False)
        rt.mark_running(blocker)

        task_a = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("x")))
        task_b = asyncio.ensure_future(client.post("/v1/chat/completions", json=_payload("y")))

        await asyncio.sleep(0.3)
        rt.complete_request(blocker)

        ra = await asyncio.wait_for(task_a, timeout=10)
        rb = await asyncio.wait_for(task_b, timeout=10)

        for r in (ra, rb):
            body = json.dumps(r.json()).lower()
            for m in ("slot_id", "tombstone", "sampling_signature", "guarded_adapter", "traceback", "token_ids"):
                assert m not in body


class TestDisabledDefault:
    async def test_disabled_without_flags(self, client, monkeypatch):
        monkeypatch.setenv("WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED", "false")
        monkeypatch.setenv("WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED", "false")
        monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "false")

        resp = await client.post("/v1/chat/completions", json=_payload("hi"))
        assert resp.status_code == 200


class TestOneFlagDisabled:
    async def test_global_only_disabled(self, client, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED", "false")
        resp = await client.post("/v1/chat/completions", json=_payload("hi"))
        assert resp.status_code == 200

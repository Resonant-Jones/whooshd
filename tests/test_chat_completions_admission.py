"""HTTP-level admission control tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.runtime import get_runtime


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _restore_lifecycle():
    """Ensure lifecycle is ready before each test."""
    rt = get_runtime()
    rt.complete_warmup()
    yield
    rt.complete_warmup()


# ── Acceptance ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_under_limit_succeeds(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_streaming_under_limit_succeeds(client):
    """Streaming under the active request limit should return 200 with SSE."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "data: [DONE]" in resp.text


# ── Overload rejection ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overloaded_non_streaming_returns_429(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=False)
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-1.5b-instruct-mlx",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "RUNNER_OVERLOADED"
    finally:
        rt.complete_request(rid)


@pytest.mark.asyncio
async def test_overloaded_streaming_returns_429_before_stream(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=True)
    rt.mark_streaming(rid)
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-1.5b-instruct-mlx",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 429
        # Must be JSON error, not SSE.
        assert resp.headers.get("content-type", "").startswith("application/json")
    finally:
        rt.complete_request(rid)


# ── Rejected request does not become active ─────────────────────────────────


@pytest.mark.asyncio
async def test_rejected_request_not_in_active_jobs(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=False)
    active_before = rt.active_jobs
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-1.5b-instruct-mlx",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 429
        assert rt.active_jobs == active_before  # unchanged
    finally:
        rt.complete_request(rid)


# ── Counters ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accepted_increments_counter(client):
    rt = get_runtime()
    before = rt.total_requests_accepted
    await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    assert rt.total_requests_accepted == before + 1


@pytest.mark.asyncio
async def test_rejected_increments_rejection_counter(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=False)
    rejected_before = rt.total_rejected_overloaded
    try:
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-1.5b-instruct-mlx",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert rt.total_rejected_overloaded == rejected_before + 1
    finally:
        rt.complete_request(rid)


# ── /runtime/admission ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_admission_returns_200(client):
    resp = await client.get("/runtime/admission")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_runtime_admission_body_shape(client):
    resp = await client.get("/runtime/admission")
    body = resp.json()
    assert "max_active_requests" in body
    assert "active_jobs" in body
    assert "max_prompt_chars" in body
    assert "max_messages" in body
    assert "max_request_max_tokens" in body
    assert "counters" in body
    assert "accepted" in body["counters"]
    assert "rejected" in body["counters"]


@pytest.mark.asyncio
async def test_runtime_admission_no_prompt_leakage(client):
    """Admission config must never expose prompt content."""
    resp = await client.get("/runtime/admission")
    data_str = str(resp.json())
    for key in ("prompt", "messages", "content", "text"):
        assert key not in resp.json()


# ── Codexify compatibility still holds ──────────────────────────────────────


@pytest.mark.asyncio
async def test_codexify_smoke_probe_still_passes(client):
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    assert result.ok is True
    assert result.streaming_visible_text == "Whoosh'd streaming stub online."

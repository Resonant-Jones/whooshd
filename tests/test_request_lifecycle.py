"""Tests for request lifecycle tracking and cancellation scaffold.

Covers:
  * RuntimeState lifecycle methods (begin / mark / complete / cancel / fail)
  * active_jobs computed from live request state
  * /health and /runtime reflect real active_jobs
  * GET /runtime/requests
  * POST /runtime/requests/{id}/cancel
  * Non-streaming and streaming request lifecycle integration
  * Request snapshots never include prompt text
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import RequestLifecycleState
from whooshd.runtime import RuntimeState


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Unit: RuntimeState lifecycle methods ────────────────────────────────────


class TestRuntimeStateLifecycle:
    def test_begin_request_returns_id(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="test-model", stream=False)
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_begin_request_sets_accepted(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        snap = rt.get_request_snapshot(rid)
        assert snap is not None
        assert snap.status == RequestLifecycleState.ACCEPTED

    def test_mark_running(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_running(rid)
        assert rt.get_request_snapshot(rid).status == RequestLifecycleState.RUNNING

    def test_mark_streaming(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_streaming(rid)
        assert rt.get_request_snapshot(rid).status == RequestLifecycleState.STREAMING

    def test_complete_request(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_running(rid)
        rt.complete_request(rid)
        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.COMPLETED
        assert snap.ended_at is not None

    def test_cancel_request(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_streaming(rid)
        rt.cancel_request(rid)
        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.CANCELLED
        assert snap.ended_at is not None

    def test_fail_request(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.fail_request(rid, error_code="TIMEOUT")
        snap = rt.get_request_snapshot(rid)
        assert snap.status == RequestLifecycleState.FAILED
        assert snap.error_code == "TIMEOUT"

    def test_get_request_snapshot_unknown_returns_none(self):
        rt = RuntimeState()
        assert rt.get_request_snapshot("nonexistent") is None

    def test_nonexistent_lifecycle_calls_do_not_raise(self):
        """Safe no-ops for unknown request IDs."""
        rt = RuntimeState()
        rt.mark_running("nonexistent")
        rt.complete_request("nonexistent")
        rt.cancel_request("nonexistent")
        rt.fail_request("nonexistent")


# ── Unit: active_jobs computed correctly ────────────────────────────────────


class TestActiveJobs:
    def test_zero_when_no_requests(self):
        rt = RuntimeState()
        assert rt.active_jobs == 0

    def test_one_after_begin(self):
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)
        assert rt.active_jobs == 1

    def test_one_when_running(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_running(rid)
        assert rt.active_jobs == 1

    def test_one_when_streaming(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_streaming(rid)
        assert rt.active_jobs == 1

    def test_zero_after_complete(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.mark_running(rid)
        rt.complete_request(rid)
        assert rt.active_jobs == 0

    def test_zero_after_cancel(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.cancel_request(rid)
        assert rt.active_jobs == 0

    def test_zero_after_fail(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.fail_request(rid)
        assert rt.active_jobs == 0

    def test_multiple_requests(self):
        rt = RuntimeState()
        rt.begin_request(model="a", stream=False)
        rt.begin_request(model="b", stream=True)
        assert rt.active_jobs == 2


# ── Unit: get_active_requests / get_all_requests ────────────────────────────


class TestRequestQueries:
    def test_get_active_requests(self):
        rt = RuntimeState()
        rt.begin_request(model="a", stream=False)
        rid2 = rt.begin_request(model="b", stream=True)
        rt.complete_request(rid2)
        # Only "a" is still active.
        active = rt.get_active_requests()
        assert len(active) == 1
        assert active[0].model == "a"

    def test_get_all_requests(self):
        rt = RuntimeState()
        rt.begin_request(model="a", stream=False)
        rid2 = rt.begin_request(model="b", stream=True)
        rt.complete_request(rid2)
        # Both "a" (active) and "b" (completed) appear.
        all_reqs = rt.get_all_requests()
        assert len(all_reqs) == 2

    def test_build_request_list(self):
        rt = RuntimeState()
        rt.begin_request(model="x", stream=False)
        resp = rt.build_request_list()
        assert len(resp.requests) == 1
        assert resp.active_count == 1


# ── Unit: request snapshots never expose prompt content ─────────────────────


class TestSnapshotPrivacy:
    def test_snapshot_has_no_prompt_field(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        snap = rt.get_request_snapshot(rid)
        data = snap.model_dump()
        assert "prompt" not in data
        assert "messages" not in data
        assert "content" not in data
        assert "input" not in data

    def test_snapshot_has_only_metadata_fields(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        snap = rt.get_request_snapshot(rid)
        data = snap.model_dump()
        permitted = {"request_id", "model", "stream", "status", "started_at", "ended_at", "error_code"}
        assert set(data.keys()) == permitted


# ── Integration: non-streaming request lifecycle via HTTP ────────────────────


@pytest.mark.asyncio
async def test_non_streaming_active_jobs_returns_to_zero(client):
    """After a non-streaming completion, /health must show active_jobs=0."""
    # Baseline
    resp = await client.get("/health")
    assert resp.json()["active_jobs"] == 0

    # Send a non-streaming request.
    await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    # After completion, active_jobs must be back to 0.
    resp = await client.get("/health")
    assert resp.json()["active_jobs"] == 0


@pytest.mark.asyncio
async def test_non_streaming_request_appears_in_runtime_requests(client):
    """A completed request should appear in the request list."""
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200

    # Check the request list.
    resp2 = await client.get("/runtime/requests")
    body = resp2.json()
    assert len(body["requests"]) >= 1
    assert body["active_count"] == 0  # all done


# ── Integration: streaming request lifecycle via HTTP ────────────────────────


@pytest.mark.asyncio
async def test_streaming_active_jobs_returns_to_zero_after_stream(client):
    """After a streaming completion, /health must show active_jobs=0."""
    # Baseline
    resp = await client.get("/health")
    assert resp.json()["active_jobs"] == 0

    # Drain a streaming request fully.
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    ) as stream_resp:
        async for _ in stream_resp.aiter_lines():
            pass

    # After stream closes, active_jobs must return to 0.
    resp = await client.get("/health")
    assert resp.json()["active_jobs"] == 0


async def test_active_jobs_incremented_during_streaming_lifecycle():
    """RuntimeState reports active_jobs > 0 while a stream is in-flight.

    This is a direct RuntimeState test because the ASGI test transport
    buffers responses — mid-stream observation requires the real runtime.
    """
    rt = RuntimeState()
    assert rt.active_jobs == 0

    rid = rt.begin_request(model="m", stream=True)
    rt.mark_streaming(rid)
    assert rt.active_jobs == 1

    rt.complete_request(rid)
    assert rt.active_jobs == 0


@pytest.mark.asyncio
async def test_streaming_request_appears_in_runtime_requests(client):
    """A streaming request should appear in the request list after completion."""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as stream_resp:
        async for _ in stream_resp.aiter_lines():
            pass

    resp = await client.get("/runtime/requests")
    body = resp.json()
    assert len(body["requests"]) >= 1


# ── Integration: GET /runtime/requests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_requests_returns_200(client):
    resp = await client.get("/runtime/requests")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_runtime_requests_body_shape(client):
    resp = await client.get("/runtime/requests")
    body = resp.json()
    assert "requests" in body
    assert isinstance(body["requests"], list)
    assert "active_count" in body
    assert isinstance(body["active_count"], int)
    assert body["active_count"] >= 0


# ── Integration: POST /runtime/requests/{id}/cancel ─────────────────────────


@pytest.mark.asyncio
async def test_cancel_unknown_request_returns_404(client):
    resp = await client.post("/runtime/requests/nonexistent/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_already_completed_request_returns_409(client):
    """Cancelling a completed request is a conflict."""
    # Create and complete a request.
    cresp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    assert cresp.status_code == 200

    # Get its ID from the request list.
    list_resp = await client.get("/runtime/requests")
    reqs = list_resp.json()["requests"]
    completed_id = reqs[-1]["request_id"]

    # Try to cancel it.
    resp = await client.post(f"/runtime/requests/{completed_id}/cancel")
    assert resp.status_code == 409


# ── Integration: request snapshots are public-safe via HTTP ─────────────────


@pytest.mark.asyncio
async def test_runtime_requests_no_prompt_leakage(client):
    """Request snapshots from /runtime/requests must not contain prompt text."""
    await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Secret prompt content"}],
            "stream": False,
        },
    )
    resp = await client.get("/runtime/requests")
    for req in resp.json()["requests"]:
        data_str = str(req)
        assert "Secret prompt content" not in data_str
        assert "prompt" not in req
        assert "messages" not in req
        assert "content" not in req


# ── Integration: /health active_jobs with multiple concurrent streams ───────


@pytest.mark.asyncio
async def test_multiple_streams_active_jobs_via_runtime():
    """Two concurrent streaming requests should show active_jobs=2.

    Direct RuntimeState test — ASGI transport buffers, so mid-stream
    observation must go through the runtime directly.
    """
    rt = RuntimeState()
    assert rt.active_jobs == 0

    rid_a = rt.begin_request(model="a", stream=True)
    rt.mark_streaming(rid_a)
    rid_b = rt.begin_request(model="b", stream=True)
    rt.mark_streaming(rid_b)
    assert rt.active_jobs == 2

    rt.complete_request(rid_a)
    assert rt.active_jobs == 1

    rt.cancel_request(rid_b)
    assert rt.active_jobs == 0

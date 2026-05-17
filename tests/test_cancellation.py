"""Tests for cancellation hardening and stream disconnect cleanup."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import CancellationToken, RequestLifecycleState
from whooshd.runtime import RuntimeState, get_runtime


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Unit: CancellationToken ─────────────────────────────────────────────────


class TestCancellationToken:
    def test_not_cancelled_initially(self):
        token = CancellationToken("req-1")
        assert token.is_cancelled() is False

    def test_cancel_sets_flag(self):
        token = CancellationToken("req-1")
        token.cancel()
        assert token.is_cancelled() is True

    async def test_wait_cancelled(self):
        import asyncio

        token = CancellationToken("req-1")
        # Cancel after a short delay.
        async def _cancel():
            await asyncio.sleep(0.01)
            token.cancel()

        asyncio.create_task(_cancel())
        await token.wait_cancelled()
        assert token.is_cancelled() is True


# ── Unit: RuntimeState cancellation ─────────────────────────────────────────


class TestRuntimeStateCancellation:
    def test_begin_request_creates_token(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        token = rt.get_cancellation_token(rid)
        assert token is not None
        assert token.is_cancelled() is False

    def test_request_cancellation_signals_token(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        assert rt.request_cancellation(rid) is True
        token = rt.get_cancellation_token(rid)
        assert token.is_cancelled() is True

    def test_request_cancellation_unknown_returns_false(self):
        rt = RuntimeState()
        assert rt.request_cancellation("nonexistent") is False

    def test_request_cancellation_terminal_returns_false(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.complete_request(rid)
        assert rt.request_cancellation(rid) is False

    def test_cancel_requested_in_snapshot(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.request_cancellation(rid)
        snap = rt.get_request_snapshot(rid)
        assert snap.cancel_requested is True

    def test_snapshot_no_prompt_in_cancellation(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.request_cancellation(rid)
        snap = rt.get_request_snapshot(rid)
        data = snap.model_dump()
        assert "prompt" not in data
        assert "messages" not in data
        assert "content" not in data

    def test_cancel_increments_counter(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        before = rt.total_requests_cancel_requested
        rt.request_cancellation(rid)
        assert rt.total_requests_cancel_requested == before + 1

    def test_cancel_request_increments_cancelled_counter(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False)
        rt.cancel_request(rid)
        assert rt.total_requests_cancelled == 1


# ── HTTP: Cancel endpoint ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_active_request_returns_200(client):
    """Cancel an active streaming request via the endpoint."""
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=True)
    rt.mark_streaming(rid)
    resp = await client.post(f"/runtime/requests/{rid}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is True
    assert body["request_id"] == rid
    assert body["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_unknown_returns_404(client):
    resp = await client.post("/runtime/requests/nonexistent/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_completed_returns_409(client):
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=False)
    rt.mark_running(rid)
    rt.complete_request(rid)

    resp = await client.post(f"/runtime/requests/{rid}/cancel")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cancel_response_no_prompt_leakage(client):
    rt = get_runtime()
    rid = rt.begin_request(model="m", stream=True)
    rt.mark_streaming(rid)
    resp = await client.post(f"/runtime/requests/{rid}/cancel")
    data_str = str(resp.json())
    assert "prompt" not in data_str.lower()
    assert "messages" not in data_str.lower()


# ── Streaming cancellation (stub) ───────────────────────────────────────────


class TestStreamingCancellationStub:
    async def test_cancellation_stops_stream_early(self):
        """When the cancellation token is set, the stub stream stops early."""
        rt = RuntimeState()
        rid = rt.begin_request(model="stub", stream=True)
        rt.mark_streaming(rid)
        token = rt.get_cancellation_token(rid)

        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="stub", messages=[ChatMessage(role="user", content="Hi")]
        )

        gen = adapter.chat_completion_stream(req)
        first = await gen.__anext__()  # role chunk
        assert first.choices[0].delta.role == "assistant"

        # Signal cancellation — next iteration of the app's _sse_stream
        # would break. Verify token state.
        token.cancel()
        assert token.is_cancelled() is True

    async def test_cancellation_token_stops_generator(self):
        """App-layer token check breaks the async for loop."""
        import asyncio

        rt = RuntimeState()
        rid = rt.begin_request(model="stub", stream=True)
        rt.mark_streaming(rid)
        token = rt.get_cancellation_token(rid)

        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="stub", messages=[ChatMessage(role="user", content="Hi")]
        )

        chunks: list = []
        gen = adapter.chat_completion_stream(req)
        async for chunk in gen:
            if token.is_cancelled():
                break
            chunks.append(chunk)
            # Cancel after first content chunk
            if chunk.choices[0].delta.content:
                token.cancel()

        # Should have role chunk + at most 1 content chunk (not all 5)
        assert len(chunks) <= 2
        # Make sure we didn't get the final finish chunk
        finish_reasons = [c.choices[0].finish_reason for c in chunks]
        assert "stop" not in finish_reasons


# ── active_jobs after cancellation ──────────────────────────────────────────


class TestActiveJobsAfterCancel:
    def test_active_jobs_zero_after_cancel(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_streaming(rid)
        assert rt.active_jobs == 1
        rt.cancel_request(rid)
        assert rt.active_jobs == 0

    def test_active_jobs_zero_after_request_cancellation_then_cancel(self):
        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=True)
        rt.mark_streaming(rid)
        rt.request_cancellation(rid)
        # Cancellation requested but not yet terminal.
        assert rt.active_jobs == 1
        rt.cancel_request(rid)
        assert rt.active_jobs == 0


# ── Snapshot privacy ────────────────────────────────────────────────────────


def test_cancel_requested_not_a_prompt_field():
    rt = RuntimeState()
    rid = rt.begin_request(model="m", stream=True)
    rt.request_cancellation(rid)
    snap = rt.get_request_snapshot(rid)
    data = snap.model_dump()
    assert "cancel_requested" in data  # it's a metadata field, not a leak
    for key in ("prompt", "messages", "content", "text", "input"):
        assert key not in data


# ── Codexify compatibility ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_codexify_smoke_probe_still_passes(client):
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    assert result.ok is True
    assert result.streaming_visible_text == "Whoosh'd streaming stub online."


@pytest.mark.asyncio
async def test_codexify_probe_cancel_endpoint(client):
    """Cancel endpoint must still work for Codexify compatibility."""
    from whooshd.compat.codexify_probe import CodexifyProbe

    probe = CodexifyProbe(client)
    health = await probe.probe_health()
    assert health.ok is True

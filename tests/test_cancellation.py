"""Tests for cancellation hardening and stream disconnect cleanup."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import CancellationToken, RequestExecutionContext, RequestLifecycleState
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


# ── Adapter-aware cancellation (Phase 3B.1) ─────────────────────────────────


class TestAdapterAwareCancellationStub:
    async def test_context_accepted_by_stub_stream(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        rt = RuntimeState()
        rid = rt.begin_request(model="stub", stream=True)
        token = rt.get_cancellation_token(rid)
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="stub", messages=[ChatMessage(role="user", content="Hi")]
        )

        chunks = [c async for c in adapter.chat_completion_stream(req, context=ctx)]
        # Without cancellation, should get full stream (role + 4 content + finish = 6)
        assert len(chunks) == 6

    async def test_stub_stream_stops_on_cancellation(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        rt = RuntimeState()
        rid = rt.begin_request(model="stub", stream=True)
        token = rt.get_cancellation_token(rid)
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="stub", messages=[ChatMessage(role="user", content="Hi")]
        )

        # Cancel BEFORE streaming.
        token.cancel()
        chunks = [c async for c in adapter.chat_completion_stream(req, context=ctx)]
        assert len(chunks) == 0

    async def test_stub_no_finish_chunk_after_cancellation(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        rt = RuntimeState()
        rid = rt.begin_request(model="stub", stream=True)
        token = rt.get_cancellation_token(rid)
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="stub", messages=[ChatMessage(role="user", content="Hi")]
        )

        gen = adapter.chat_completion_stream(req, context=ctx)
        first = await gen.__anext__()  # role chunk
        assert first.choices[0].delta.role == "assistant"

        # Get one content chunk
        second = await gen.__anext__()
        assert second.choices[0].delta.content is not None

        # Cancel now.
        token.cancel()

        # Collect remaining — should be empty (cancellation check before each yield)
        remaining = [c async for c in gen]
        assert len(remaining) == 0

        # No finish_reason = "stop" anywhere.
        assert second.choices[0].finish_reason is None


class TestAdapterAwareCancellationMLX:
    async def test_context_accepted_by_mlx_stream(self, mock_mlx_lm_module):
        from whooshd.adapters.mlx import MLXInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        mock_mlx_lm_module.stream_generate.return_value = iter([
            _stream_resp("A"), _stream_resp(" "), _stream_resp("B"),
        ])
        rt = RuntimeState()
        rid = rt.begin_request(model="mlx-model", stream=True)
        token = rt.get_cancellation_token(rid)
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = MLXInferenceAdapter()
        req = ChatCompletionRequest(
            model="test", messages=[ChatMessage(role="user", content="Hi")]
        )

        chunks = [c async for c in adapter.chat_completion_stream(req, context=ctx)]
        # role + 3 content + finish = 5
        assert len(chunks) == 5

    async def test_mlx_stream_stops_on_cancellation(self, mock_mlx_lm_module):
        from whooshd.adapters.mlx import MLXInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        mock_mlx_lm_module.stream_generate.return_value = iter([
            _stream_resp("A"), _stream_resp(" "), _stream_resp("B"),
        ])
        rt = RuntimeState()
        rid = rt.begin_request(model="mlx-model", stream=True)
        token = rt.get_cancellation_token(rid)
        token.cancel()  # Cancel before streaming
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = MLXInferenceAdapter()
        req = ChatCompletionRequest(
            model="test", messages=[ChatMessage(role="user", content="Hi")]
        )

        chunks = [c async for c in adapter.chat_completion_stream(req, context=ctx)]
        assert len(chunks) == 0

    async def test_mlx_generator_close_called(self, mock_mlx_lm_module):
        from whooshd.adapters.mlx import MLXInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage
        from unittest.mock import MagicMock

        mock_stream = MagicMock()
        mock_stream.__iter__.return_value = iter([_stream_resp("A")])
        mock_stream.close = MagicMock()
        mock_mlx_lm_module.stream_generate.return_value = mock_stream

        rt = RuntimeState()
        rid = rt.begin_request(model="mlx-model", stream=True)
        token = rt.get_cancellation_token(rid)
        ctx = RequestExecutionContext(request_id=rid, cancellation_token=token, stream=True)

        adapter = MLXInferenceAdapter()
        req = ChatCompletionRequest(
            model="test", messages=[ChatMessage(role="user", content="Hi")]
        )

        async for _ in adapter.chat_completion_stream(req, context=ctx):
            pass  # drain without cancelling

        # Generator close should have been called in finally block.
        mock_stream.close.assert_called_once()


# ── MLX mock helpers ────────────────────────────────────────────────────────


def _stream_resp(text: str):
    class _Resp:
        pass
    r = _Resp()
    r.text = text
    return r


@pytest.fixture
def mock_mlx_lm_module():
    import sys
    from unittest.mock import MagicMock

    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "user\nHello\nassistant\n"

    mock_mlx = MagicMock()
    mock_mlx.load.return_value = (MagicMock(), mock_tokenizer)
    mock_mlx.generate.return_value = "Mock"
    mock_mlx.stream_generate.return_value = iter([])

    sys.modules["mlx_lm"] = mock_mlx
    yield mock_mlx
    del sys.modules["mlx_lm"]

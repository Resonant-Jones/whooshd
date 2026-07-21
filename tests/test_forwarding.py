"""Integration-style tests for HTTP forwarding with mock upstream servers.

Tests cover:
  * llama.cpp streaming/non-streaming forwarding
  * MLX-LM Server streaming/non-streaming forwarding
  * Router dispatches real inference through both adapters
  * Upstream error classification
  * Connection refused → RuntimeUnavailable
  * Timeout handling
  * Client disconnect stops upstream stream
  * Request body preserves model field
  * SSE chunk parsing from mock upstream
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ErrorCode,
    ErrorResponse,
    RequestExecutionContext,
)
from whooshd.http_forwarding import (
    UpstreamBadRequest,
    UpstreamConnectionError,
    UpstreamHTTPError,
    UpstreamRuntimeError,
    UpstreamTimeoutError,
    RuntimeUnavailable,
    RuntimeWarming,
    StreamInterrupted,
    _serialize_message,
    build_forward_body,
    forward_non_streaming,
    forward_streaming,
)
from whooshd.routing import RuntimeRouter, RuntimeKind, get_router, reset_router


# ── Mock upstream response helpers ──────────────────────────────────────────


def _mock_sse_chunks(chunks: list[dict]) -> str:
    """Build a full SSE response body from a list of chunk dicts."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
    return "\n".join(lines) + "\n\n"


def _make_mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text_data: str | None = None,
    headers: dict | None = None,
):
    """Build a MagicMock that acts like an httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "application/json"}
    if json_data is not None:
        resp.json.return_value = json_data
        resp.text = json.dumps(json_data)
    elif text_data is not None:
        resp.text = text_data
        resp.json.side_effect = json.JSONDecodeError("no json", "", 0)
    else:
        resp.text = ""
        resp.json.side_effect = json.JSONDecodeError("no json", "", 0)
    return resp


def _make_mock_stream_response(
    status_code: int = 200,
    sse_lines: list[str] | None = None,
    headers: dict | None = None,
):
    """Build a mock async context manager that streams SSE lines."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "text/event-stream"}

    # Make resp an async context manager for `async with client.stream(...) as resp:`
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)

    if sse_lines is not None:
        resp.aiter_lines = MagicMock(return_value=_async_iter(sse_lines))
    else:
        resp.aiter_lines = MagicMock(return_value=_async_iter([]))

    resp.aclose = AsyncMock()
    return resp


async def _async_iter(items: list):
    """Async generator from a list."""
    for item in items:
        yield item


def _mk_chunk(id: str = "chatcmpl-001", model: str = "test-model",
              content: str = "", role: str | None = None,
              finish_reason: str | None = None, created: int = 1700000000) -> dict:
    """Build an OpenAI-compatible SSE chunk dict."""
    delta: dict = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    return {
        "id": id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


def _mk_completion_response(id: str = "chatcmpl-001", model: str = "test-model",
                             content: str = "Hello!") -> dict:
    """Build an OpenAI-compatible non-streaming completion response."""
    return {
        "id": id,
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
        },
    }


# ── Shared test request ─────────────────────────────────────────────────────

def _make_req(model: str = "test-model", stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
        stream=stream,
    )


# ── Forward body builder tests ──────────────────────────────────────────────


class TestBuildForwardBody:
    def test_basic_body(self):
        req = _make_req()
        body = build_forward_body(req)
        assert body["model"] == "test-model"
        assert body["stream"] is False
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "Hello"

    def test_model_override(self):
        req = _make_req(model="client-model")
        body = build_forward_body(req, model_override="server-model")
        assert body["model"] == "server-model"

    def test_preserves_temperature_top_p(self):
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            temperature=0.3, top_p=0.5, stream=False,
        )
        body = build_forward_body(req)
        assert body["temperature"] == 0.3
        assert body["top_p"] == 0.5

    def test_skips_default_temperature(self):
        """temperature=0.7 (OpenAI default) is forwarded since it's a non-None value.
        
        Upstream servers treat explicit temperature=0.7 identically to the default.
        """
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            temperature=0.7, stream=False,  # 0.7 is the standard default
        )
        body = build_forward_body(req)
        # With the schema change, all non-None fields are forwarded.
        assert body["temperature"] == 0.7

    def test_includes_max_tokens(self):
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            max_tokens=512, stream=False,
        )
        body = build_forward_body(req)
        assert body["max_tokens"] == 512


# ── Non-streaming forwarding tests ──────────────────────────────────────────


class TestForwardNonStreaming:
    def test_200_returns_chat_completion_response(self):
        """Successful upstream response is parsed into ChatCompletionResponse."""
        mock_data = _mk_completion_response(content="Hello from upstream!")
        mock_resp = _make_mock_response(200, json_data=mock_data)

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                result = await forward_non_streaming(
                    "http://127.0.0.1:8080", _make_req(),
                )
                assert isinstance(result, ChatCompletionResponse)
                assert result.choices[0].message.content == "Hello from upstream!"

            asyncio.run(_run())

    def test_400_raises_upstream_bad_request(self):
        """Upstream 400 → UpstreamBadRequest."""
        mock_resp = _make_mock_response(400, json_data={"error": "bad request"})

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamBadRequest):
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())

            asyncio.run(_run())

    def test_404_raises_model_not_found(self):
        """Upstream 404 raises an error with 404 mapping."""
        from whooshd.http_forwarding import RuntimeModelNotFound
        mock_resp = _make_mock_response(404, json_data={"error": "not found"})

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(RuntimeModelNotFound):
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())

            asyncio.run(_run())

    def test_429_raises_runtime_warming(self):
        """Upstream 429 → RuntimeWarming (model loading)."""
        mock_resp = _make_mock_response(429, json_data={"error": "too many requests"})

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(RuntimeWarming):
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())

            asyncio.run(_run())

    def test_502_raises_upstream_error(self):
        """Upstream 502 → UpstreamHTTPError with 502 http_status."""
        mock_resp = _make_mock_response(502, text_data="bad gateway")

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamHTTPError) as exc_info:
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())
                assert exc_info.value.http_status == 502

            asyncio.run(_run())

    def test_connection_refused_raises_upstream_connection_error(self):
        """Connection refused → UpstreamConnectionError (503)."""
        import httpx

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamConnectionError) as exc_info:
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())
                assert exc_info.value.http_status == 503

            asyncio.run(_run())

    def test_timeout_raises_upstream_timeout_error(self):
        """Read timeout → UpstreamTimeoutError (504)."""
        import httpx

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamTimeoutError) as exc_info:
                    await forward_non_streaming("http://127.0.0.1:8080", _make_req())
                assert exc_info.value.http_status == 504

            asyncio.run(_run())


# ── Streaming forwarding tests ──────────────────────────────────────────────


class TestForwardStreaming:
    def test_200_streams_sse_chunks(self):
        """Successful streaming response yields ChatCompletionChunks."""
        sse = _mock_sse_chunks([
            _mk_chunk(role="assistant"),
            _mk_chunk(content="Hello "),
            _mk_chunk(content="world!"),
            _mk_chunk(finish_reason="stop"),
        ])
        lines = sse.split("\n")

        mock_resp = _make_mock_stream_response(200, sse_lines=lines)

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in forward_streaming(
                    "http://127.0.0.1:8080", _make_req(stream=True),
                ):
                    chunks.append(chunk)
                # Should have 4 content-bearing chunks (excluding [DONE]).
                assert len(chunks) == 4
                assert isinstance(chunks[0], ChatCompletionChunk)
                # First chunk has role.
                assert chunks[0].choices[0].delta.role == "assistant"
                # Last chunk has finish_reason.
                assert chunks[3].choices[0].finish_reason == "stop"

            asyncio.run(_run())

    def test_stream_skips_done_sentinel(self):
        """[DONE] lines are skipped by the forwarder."""
        sse_lines = [
            "data: [DONE]",
        ]

        mock_resp = _make_mock_stream_response(200, sse_lines=sse_lines)

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in forward_streaming(
                    "http://127.0.0.1:8080", _make_req(stream=True),
                ):
                    chunks.append(chunk)
                assert len(chunks) == 0  # Only [DONE] was present.

            asyncio.run(_run())

    def test_stream_handles_parse_errors_gracefully(self):
        """Unparseable SSE lines are skipped without crashing the stream."""
        sse_lines = [
            "data: {\"id\":\"c001\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"m\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"},\"finish_reason\":null}]}",
            "data: {invalid json!!!}",
            "data: {\"id\":\"c001\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"m\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"more\"},\"finish_reason\":null}]}",
        ]

        mock_resp = _make_mock_stream_response(200, sse_lines=sse_lines)

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in forward_streaming(
                    "http://127.0.0.1:8080", _make_req(stream=True),
                ):
                    chunks.append(chunk)
                # Should have 2 valid chunks (bad line skipped).
                assert len(chunks) == 2

            asyncio.run(_run())

    def test_stream_connection_refused(self):
        """Connection refused in streaming → UpstreamConnectionError."""
        import httpx

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(side_effect=httpx.ConnectError("refused"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamConnectionError):
                    async for _ in forward_streaming(
                        "http://127.0.0.1:8080", _make_req(stream=True),
                    ):
                        pass

            asyncio.run(_run())

    def test_stream_cancellation_stops_yielding(self):
        """Cancellation token checked between chunks stops the stream."""
        from whooshd.contracts import CancellationToken

        sse = _mock_sse_chunks([
            _mk_chunk(content="Hello "),
            _mk_chunk(content="world!"),
            _mk_chunk(content="extra"),
        ])
        lines = sse.split("\n")

        cancel_token = CancellationToken(request_id="test-req")
        mock_resp = _make_mock_stream_response(200, sse_lines=lines)

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in forward_streaming(
                    "http://127.0.0.1:8080", _make_req(stream=True),
                    cancellation_token=cancel_token,
                ):
                    chunks.append(chunk)
                    if len(chunks) == 1:
                        cancel_token.cancel()  # Cancel after first chunk.
                # Should have stopped after the first chunk.
                assert len(chunks) == 1

            asyncio.run(_run())

    def test_stream_upstream_error_status(self):
        """Non-200 status code on streaming → UpstreamHTTPError."""
        mock_resp = _make_mock_stream_response(500)
        # Override — make aiter_lines not available since we raise before iteration.
        mock_resp.aiter_lines = MagicMock()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                with pytest.raises(UpstreamHTTPError) as exc_info:
                    async for _ in forward_streaming(
                        "http://127.0.0.1:8080", _make_req(stream=True),
                    ):
                        pass
                assert exc_info.value.http_status == 502

            asyncio.run(_run())


# ── llama.cpp adapter forwarding (mocked upstream) ──────────────────────────


class TestLlamaCppAdapterForwarding:
    """Test llama.cpp adapter forwards to a mock upstream server."""

    def _make_configured_adapter(self):
        """Return a LlamaCppAdapter configured with a server URL, with httpx mocked."""
        from whooshd.adapters.llama_cpp import LlamaCppAdapter, LlamaCppAdapterConfig

        config = LlamaCppAdapterConfig(
            server_url="http://127.0.0.1:8080",
            model_path="/models/test.gguf",
            health_timeout_seconds=1.0,
        )
        return LlamaCppAdapter(config=config)

    def test_non_streaming_forwards_and_returns_json(self):
        """Non-streaming request is forwarded and response parsed."""
        adapter = self._make_configured_adapter()
        mock_resp = _make_mock_response(200, json_data=_mk_completion_response(
            model="/models/test.gguf", content="Hello from GGUF!",
        ))

        # Make health probe return reachable.
        from whooshd.adapters.llama_cpp import _LlamaCppHealthStatus
        adapter.check_health = AsyncMock(return_value=_LlamaCppHealthStatus(
            reachable=True, runner_status="ready",
            model_lifecycle="ready", detail="llama.cpp server is ready.",
        ))

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                result = await adapter.chat_completion(_make_req(model="/models/test.gguf"))
                assert isinstance(result, ChatCompletionResponse)
                assert result.choices[0].message.content == "Hello from GGUF!"

            asyncio.run(_run())

    def test_streaming_forwards_sse_chunks(self):
        """Streaming request forwards and yields ChatCompletionChunks."""
        adapter = self._make_configured_adapter()

        sse = _mock_sse_chunks([
            _mk_chunk(role="assistant", model="/models/test.gguf"),
            _mk_chunk(content="Hello ", model="/models/test.gguf"),
            _mk_chunk(content="GGUF!", model="/models/test.gguf"),
            _mk_chunk(finish_reason="stop", model="/models/test.gguf"),
        ])
        lines = sse.split("\n")

        mock_stream_resp = _make_mock_stream_response(200, sse_lines=lines)

        # Make health probe return reachable.
        from whooshd.adapters.llama_cpp import _LlamaCppHealthStatus
        adapter.check_health = AsyncMock(return_value=_LlamaCppHealthStatus(
            reachable=True, runner_status="ready",
            model_lifecycle="ready", detail="ready.",
        ))

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_stream_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in adapter.chat_completion_stream(
                    _make_req(model="/models/test.gguf", stream=True),
                ):
                    chunks.append(chunk)
                assert len(chunks) == 4
                assert chunks[1].choices[0].delta.content == "Hello "

            asyncio.run(_run())

    def test_no_server_url_raises_runtime_unavailable(self):
        """Adapter without server URL raises RuntimeUnavailable for inference."""
        from whooshd.adapters.llama_cpp import LlamaCppAdapter

        adapter = LlamaCppAdapter()  # No server URL
        async def _run():
            with pytest.raises(RuntimeUnavailable):
                await adapter.chat_completion(_make_req())
        asyncio.run(_run())


# ── MLX-LM Server adapter forwarding (mocked upstream) ──────────────────────


class TestMlxLmServerAdapterForwarding:
    """Test MLX-LM Server adapter forwards to a mock upstream server."""

    def _make_configured_adapter(self):
        """Return a MlxLmServerAdapter with a mock managed process running."""
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=8081,
            model="mlx-community/test-model",
            health_timeout_seconds=1.0,
        )
        adapter = MlxLmServerAdapter(config=config)
        # Simulate a running managed process.
        adapter._managed_process = MagicMock()
        adapter._managed_process.is_running = True
        adapter._managed_process.check_exited.return_value = False
        return adapter

    def test_non_streaming_forwards_and_returns_json(self):
        """Non-streaming request forwarded to mlx_lm.server."""
        adapter = self._make_configured_adapter()
        mock_resp = _make_mock_response(200, json_data=_mk_completion_response(
            model="mlx-community/test-model", content="Hello from MLX!",
        ))

        # Make health probe return reachable.
        from whooshd.adapters.mlx_lm_server import _MlxLmServerHealthStatus
        adapter.check_health = AsyncMock(return_value=_MlxLmServerHealthStatus(
            reachable=True, runner_status="ready",
            model_lifecycle="ready", detail="ready.",
        ))

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                result = await adapter.chat_completion(
                    _make_req(model="mlx-community/test-model"),
                )
                assert isinstance(result, ChatCompletionResponse)
                assert result.choices[0].message.content == "Hello from MLX!"

            asyncio.run(_run())

    def test_streaming_forwards_sse_chunks(self):
        """Streaming request forwarded to mlx_lm.server."""
        adapter = self._make_configured_adapter()

        sse = _mock_sse_chunks([
            _mk_chunk(role="assistant", model="mlx-community/test-model"),
            _mk_chunk(content="Hello ", model="mlx-community/test-model"),
            _mk_chunk(content="MLX!", model="mlx-community/test-model"),
            _mk_chunk(finish_reason="stop", model="mlx-community/test-model"),
        ])
        lines = sse.split("\n")

        mock_stream_resp = _make_mock_stream_response(200, sse_lines=lines)

        # Make health probe return reachable.
        from whooshd.adapters.mlx_lm_server import _MlxLmServerHealthStatus
        adapter.check_health = AsyncMock(return_value=_MlxLmServerHealthStatus(
            reachable=True, runner_status="ready",
            model_lifecycle="ready", detail="ready.",
        ))

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=mock_stream_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                chunks = []
                async for chunk in adapter.chat_completion_stream(
                    _make_req(model="mlx-community/test-model", stream=True),
                ):
                    chunks.append(chunk)
                assert len(chunks) == 4
                assert chunks[2].choices[0].delta.content == "MLX!"

            asyncio.run(_run())

    def test_disabled_adapter_raises_runtime_unavailable(self):
        """Disabled MLX-LM Server raises RuntimeUnavailable."""
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(enabled=False)
        adapter = MlxLmServerAdapter(config=config)

        async def _run():
            with pytest.raises(RuntimeUnavailable):
                await adapter.chat_completion(_make_req())
        asyncio.run(_run())


# ── Router dispatches to adapter ────────────────────────────────────────────


class TestRouterDispatch:
    """Router resolves model → adapter and dispatches inference."""

    def test_router_dispatches_to_llama_cpp(self):
        """Router dispatches GGUF model requests to llama.cpp adapter."""
        from whooshd.adapters.llama_cpp import LlamaCppAdapter, LlamaCppAdapterConfig, _LlamaCppHealthStatus

        config = LlamaCppAdapterConfig(
            server_url="http://127.0.0.1:8080",
            model_path="/models/test.gguf",
            health_timeout_seconds=1.0,
        )
        adapter = LlamaCppAdapter(config=config)
        adapter.check_health = AsyncMock(return_value=_LlamaCppHealthStatus(
            reachable=True, runner_status="ready",
            model_lifecycle="ready", detail="ready.",
        ))

        mock_resp = _make_mock_response(200, json_data=_mk_completion_response(
            model="/models/test.gguf", content="Routed!",
        ))

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                router = RuntimeRouter()
                router.register(adapter)
                result = await router.chat_completion(
                    _make_req(model="/models/test.gguf"),
                )
                assert result.choices[0].message.content == "Routed!"

            asyncio.run(_run())


# ── Error classification in app layer ───────────────────────────────────────


class TestAppErrorClassification:
    """Upstream errors are properly classified into HTTP responses."""

    @pytest.mark.asyncio
    async def test_upstream_connection_error_becomes_503(self):
        """UpstreamConnectionError → 503 JSON response."""
        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app, reset_router, _init_router
        from whooshd.routing import reset_router as routing_reset

        # Set up router with a llama.cpp adapter pointing to a bad server.
        routing_reset()
        from whooshd.adapters.llama_cpp import LlamaCppAdapter, LlamaCppAdapterConfig

        config = LlamaCppAdapterConfig(
            server_url="http://127.0.0.1:19999",  # Nothing listening here
            model_path="/models/test.gguf",
            health_timeout_seconds=0.5,
        )
        adapter = LlamaCppAdapter(config=config)

        import httpx as real_httpx
        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            # Health probe → connection refused.
            mock_client.get = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_client.post = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            # Rebuild router
            routing_reset()
            router = get_router()
            router.register(adapter)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/v1/chat/completions", json={
                    "model": "/models/test.gguf",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                })
                assert resp.status_code == 503
                body = resp.json()
                assert body["code"] == ErrorCode.RUNTIME_UNAVAILABLE.value

        # Cleanup
        routing_reset()
        _init_router()


# ── Field preservation tests ───────────────────────────────────────────────


class TestFieldPreservation:
    """All OpenAI-compatible fields survive the request → forward body pipeline."""

    def test_tools_field_forwarded(self):
        """tools field is preserved in the forward body."""
        req = ChatCompletionRequest(
            model="m",
            messages=[ChatMessage(role="user", content="H")],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )
        body = build_forward_body(req)
        assert "tools" in body
        assert body["tools"][0]["function"]["name"] == "get_weather"

    def test_tool_choice_field_forwarded(self):
        """tool_choice is preserved."""
        for choice in ["auto", "none", "required"]:
            req = ChatCompletionRequest(
                model="m", messages=[ChatMessage(role="user", content="H")],
                tool_choice=choice,
            )
            body = build_forward_body(req)
            assert body["tool_choice"] == choice

    def test_tool_choice_dict_forwarded(self):
        """tool_choice as a specific tool dict is preserved."""
        specific = {"type": "function", "function": {"name": "my_tool"}}
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            tool_choice=specific,
        )
        body = build_forward_body(req)
        assert body["tool_choice"] == specific

    def test_parallel_tool_calls_forwarded(self):
        """parallel_tool_calls is preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            parallel_tool_calls=True,
        )
        body = build_forward_body(req)
        assert body["parallel_tool_calls"] is True

        req2 = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            parallel_tool_calls=False,
        )
        body2 = build_forward_body(req2)
        assert body2["parallel_tool_calls"] is False

    def test_response_format_forwarded(self):
        """response_format is preserved."""
        for fmt in [{"type": "json_object"}, {"type": "json_schema", "json_schema": {"name": "test"}}]:
            req = ChatCompletionRequest(
                model="m", messages=[ChatMessage(role="user", content="H")],
                response_format=fmt,
            )
            body = build_forward_body(req)
            assert body["response_format"] == fmt

    def test_seed_forwarded(self):
        """seed is preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            seed=42,
        )
        body = build_forward_body(req)
        assert body["seed"] == 42

    def test_presence_penalty_forwarded(self):
        """presence_penalty is preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            presence_penalty=0.5,
        )
        body = build_forward_body(req)
        assert body["presence_penalty"] == 0.5

    def test_frequency_penalty_forwarded(self):
        """frequency_penalty is preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            frequency_penalty=0.3,
        )
        body = build_forward_body(req)
        assert body["frequency_penalty"] == 0.3

    def test_logit_bias_forwarded(self):
        """logit_bias is preserved."""
        bias = {"12345": 1.0, "67890": -1.0}
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            logit_bias=bias,
        )
        body = build_forward_body(req)
        assert body["logit_bias"] == bias

    def test_logprobs_forwarded(self):
        """logprobs and top_logprobs are preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            logprobs=True, top_logprobs=5,
        )
        body = build_forward_body(req)
        assert body["logprobs"] is True
        assert body["top_logprobs"] == 5

    def test_reasoning_effort_forwarded(self):
        """reasoning_effort is preserved."""
        for level in ["low", "medium", "high"]:
            req = ChatCompletionRequest(
                model="m", messages=[ChatMessage(role="user", content="H")],
                reasoning_effort=level,
            )
            body = build_forward_body(req)
            assert body["reasoning_effort"] == level

    def test_metadata_is_internal(self):
        """Generic request metadata never reaches an upstream body."""
        meta = {"source": "codexify", "session_id": "abc123"}
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            metadata=meta,
        )
        body = build_forward_body(req)
        assert "metadata" not in body

    def test_max_completion_tokens_forwarded(self):
        """max_completion_tokens is preserved."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            max_completion_tokens=1024,
        )
        body = build_forward_body(req)
        assert body["max_completion_tokens"] == 1024

    def test_tool_call_message_fields_forwarded(self):
        """ChatMessage tool_calls and tool_call_id are preserved."""
        msg = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
        )
        d = _serialize_message(msg)
        assert "tool_calls" in d
        assert d["tool_calls"][0]["id"] == "call_1"

        msg2 = ChatMessage(role="tool", content="result", tool_call_id="call_1")
        d2 = _serialize_message(msg2)
        assert d2["tool_call_id"] == "call_1"

    def test_unknown_extra_fields_are_not_forwarded(self):
        """Unknown fields are retained at ingress but stripped for execution."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            custom_field="custom_value",
            another_extra=123,
            nested_extra={"key": "value"},
        )
        assert req.extra_fields == {
            "custom_field": "custom_value",
            "another_extra": 123,
            "nested_extra": {"key": "value"},
        }

        body = build_forward_body(req)
        assert "custom_field" not in body
        assert "another_extra" not in body
        assert "nested_extra" not in body

    def test_null_fields_not_forwarded(self):
        """Fields explicitly set to None are NOT forwarded."""
        req = ChatCompletionRequest(
            model="m", messages=[ChatMessage(role="user", content="H")],
            tools=None,
            tool_choice=None,
            seed=None,
        )
        body = build_forward_body(req)
        assert "tools" not in body
        assert "tool_choice" not in body
        assert "seed" not in body

    def test_tools_body_preserved_through_full_pipeline(self):
        """End-to-end: tools field reaches the mock upstream."""
        from whooshd.adapters.llama_cpp import LlamaCppAdapter, LlamaCppAdapterConfig, _LlamaCppHealthStatus

        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        adapter = LlamaCppAdapter(LlamaCppAdapterConfig(
            server_url="http://127.0.0.1:8080", model_path="/models/test.gguf"))
        adapter.check_health = AsyncMock(return_value=_LlamaCppHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ready"))

        mock_resp = _make_mock_response(200, json_data=_mk_completion_response())

        import asyncio
        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                req = ChatCompletionRequest(
                    model="/models/test.gguf",
                    messages=[ChatMessage(role="user", content="H")],
                    tools=tools, tool_choice="auto",
                    response_format={"type": "json_object"},
                    reasoning_effort="medium",
                    max_completion_tokens=512,
                    parallel_tool_calls=False,
                    metadata={"source": "test"},
                )
                await adapter.chat_completion(req)

                # Verify the forwarded body contains canonical fields while
                # generic metadata remains internal.
                call_args = mock_client.post.call_args
                forwarded_body = call_args.kwargs["json"]
                assert forwarded_body["tools"] == tools
                assert forwarded_body["tool_choice"] == "auto"
                assert forwarded_body["response_format"] == {"type": "json_object"}
                assert forwarded_body["reasoning_effort"] == "medium"
                assert forwarded_body["max_completion_tokens"] == 512
                assert forwarded_body["parallel_tool_calls"] is False
                assert "metadata" not in forwarded_body

            asyncio.run(_run())

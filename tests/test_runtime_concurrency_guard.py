"""Tests for per-runtime concurrency guardrails.

Tests the adapter's concurrency semaphore directly (not through HTTP)
to verify slot acquisition, release, and overload behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whooshd.contracts import ChatCompletionRequest, ChatMessage
from whooshd.http_forwarding import RuntimeOverloaded


# Shorter timeout for fast concurrency tests.
_FAST_ACQUIRE_TIMEOUT = 0.5


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_adapter(max_concurrent: int = 1, model: str = "mlx-community/test-model",
                  acquire_timeout: float = 1.0):
    """Create an MLX-LM Server adapter with a configured semaphore.

    Mock check_health so it reports ready.
    Uses a short acquire timeout for fast tests.
    """
    from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig
    from whooshd.adapters.mlx_lm_server import _MlxLmServerHealthStatus
    from whooshd.config import get_runtime_acquire_timeout_seconds

    config = MlxLmServerConfig(enabled=True, host="127.0.0.1", port=8081, model=model)
    adapter = MlxLmServerAdapter(config=config)
    adapter._max_concurrent = max_concurrent
    adapter._concurrency_semaphore = asyncio.Semaphore(max_concurrent)
    adapter.check_health = AsyncMock(return_value=_MlxLmServerHealthStatus(
        reachable=True, runner_status="ready",
        model_lifecycle="ready", detail="ready.",
    ))
    return adapter


def _make_req(model: str = "mlx-community/test-model") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
        stream=False,
    )


def _make_mock_upstream():
    """Create a mock upstream that returns a valid completion."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    return resp


def _mock_httpx(mock_resp):
    """Patch whooshd.http_forwarding.httpx to return mock_resp."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return patch("whooshd.http_forwarding.httpx", AsyncClient=MagicMock(return_value=mock_client))


# ── Concurrency guard tests ────────────────────────────────────────────────


class TestConcurrencyGuard:
    def test_max_1_allows_first_request(self, monkeypatch):
        """With max_concurrent=1, the first request succeeds."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=1)
        mock_resp = _make_mock_upstream()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                result = await adapter.chat_completion(_make_req())
                assert result.choices[0].message.content == "Hi"

            asyncio.run(_run())

    def test_second_concurrent_fails_with_overloaded(self, monkeypatch):
        """With max_concurrent=1, a second concurrent request gets RuntimeOverloaded."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=1)

        hold = asyncio.Event()

        async def blocking_post(*a, **kw):
            await hold.wait()
            return _make_mock_upstream()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = blocking_post
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                # Fire first request — acquires slot, blocks on upstream.
                t1 = asyncio.create_task(adapter.chat_completion(_make_req()))
                await asyncio.sleep(0.1)  # Let t1 acquire semaphore.

                # Second request — should be rejected (semaphore exhausted).
                # _acquire_slot will time out after WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS (default 5s).
                with pytest.raises(RuntimeOverloaded, match="capacity"):
                    await adapter.chat_completion(_make_req())

                hold.set()
                await t1

            asyncio.run(_run())

    def test_max_2_allows_two_concurrent(self, monkeypatch):
        """With max_concurrent=2, two concurrent requests succeed."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=2)

        hold = asyncio.Event()

        async def blocking_post(*a, **kw):
            await hold.wait()
            return _make_mock_upstream()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = blocking_post
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                t1 = asyncio.create_task(adapter.chat_completion(_make_req()))
                t2 = asyncio.create_task(adapter.chat_completion(_make_req()))
                await asyncio.sleep(0.2)

                # Both should have acquired slots (they're waiting on upstream).
                # Third request should be rejected (semaphore exhausted).
                with pytest.raises(RuntimeOverloaded, match="capacity"):
                    await adapter.chat_completion(_make_req())

                hold.set()
                r1 = await t1
                r2 = await t2
                assert r1.choices[0].message.content == "Hi"
                assert r2.choices[0].message.content == "Hi"

            asyncio.run(_run())


# ── Slot release tests ─────────────────────────────────────────────────────


class TestSlotRelease:
    def test_non_streaming_completion_releases_slot(self, monkeypatch):
        """After non-streaming completes, the slot is free for the next request."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=1)
        mock_resp = _make_mock_upstream()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                r1 = await adapter.chat_completion(_make_req())
                assert r1.choices[0].message.content == "Hi"

                # Slot should be released. Second request should succeed.
                r2 = await adapter.chat_completion(_make_req())
                assert r2.choices[0].message.content == "Hi"

            asyncio.run(_run())

    def test_non_streaming_error_releases_slot(self, monkeypatch):
        """After non-streaming fails, the slot is released."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=1)

        # Make check_health fail for the first call only.
        call_count = [0]

        async def flaky_health():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("health fail")
            from whooshd.adapters.mlx_lm_server import _MlxLmServerHealthStatus
            return _MlxLmServerHealthStatus(
                reachable=True, runner_status="ready",
                model_lifecycle="ready", detail="ready.",
            )

        adapter.check_health = flaky_health
        mock_resp = _make_mock_upstream()

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                # First request: health fails, slot should be released.
                with pytest.raises(RuntimeError, match="health fail"):
                    await adapter.chat_completion(_make_req())

                # Second request: should succeed (slot was released).
                r2 = await adapter.chat_completion(_make_req())
                assert r2.choices[0].message.content == "Hi"

            asyncio.run(_run())

    def test_streaming_error_releases_slot(self, monkeypatch):
        """After streaming fails, the slot is released."""
        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", str(_FAST_ACQUIRE_TIMEOUT))
        adapter = _make_adapter(max_concurrent=1)
        mock_resp = _make_mock_upstream()

        # First streaming call: upstream fails.
        call_count = [0]

        async def flaky_stream(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("stream fail")
            # Return valid chunks for second call.
            from whooshd.contracts import ChatCompletionChunk, ChatCompletionChunkChoice, ChatCompletionDelta
            yield ChatCompletionChunk(
                id="c1", created=1, model="m",
                choices=[ChatCompletionChunkChoice(index=0, delta=ChatCompletionDelta(content="Hi"))],
            )

        # Override the adapter's chat_completion_stream to wrap the semaphore logic.
        original_stream = adapter.chat_completion_stream

        async def guarded_stream(*a, **kw):
            from whooshd.config import get_runtime_acquire_timeout_seconds
            from whooshd.adapters.mlx_lm_server import _acquire_slot
            from whooshd.http_forwarding import RuntimeOverloaded

            timeout = get_runtime_acquire_timeout_seconds()
            acquired = await _acquire_slot(adapter._concurrency_semaphore, timeout)
            if not acquired:
                raise RuntimeOverloaded("at capacity")
            try:
                async for chunk in flaky_stream(*a, **kw):
                    yield chunk
            finally:
                adapter._concurrency_semaphore.release()

        adapter.chat_completion_stream = guarded_stream

        async def _run():
            # First call: stream fails, slot should be released.
            with pytest.raises(RuntimeError, match="stream fail"):
                async for _ in adapter.chat_completion_stream(_make_req()):
                    pass

            # Second call: should succeed (slot released).
            chunks = []
            async for chunk in adapter.chat_completion_stream(_make_req()):
                chunks.append(chunk)
            assert len(chunks) == 1
            assert chunks[0].choices[0].delta.content == "Hi"

        asyncio.run(_run())

"""Tests for the Codexify provider smoke script.

Validates smoke script logic using mocked HTTP responses.
No real runtimes required.
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add scripts to path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from codexify_provider_smoke import (
    Check,
    Status,
    check_health,
    check_health_runtime_stable,
    check_ready,
    check_models,
    check_tags,
    check_streaming_chat,
    parse_codexify_sse,
    _build_image_messages,
    run_smoke,
    EXIT_OK,
    EXIT_FAIL,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_client(responses: dict) -> MagicMock:
    """Build a mock httpx.AsyncClient that returns canned responses."""
    client = MagicMock()
    client.timeout = 30.0

    async def _get(path):
        key = f"GET {path}"
        if key in responses:
            resp = MagicMock()
            resp.status_code = 200
            data = responses[key]
            resp.json.return_value = data
            resp.text = json.dumps(data)
            return resp
        resp = MagicMock()
        resp.status_code = 404
        resp.json.side_effect = Exception("not found")
        return resp

    async def _post(path, *, json=None, timeout=None):
        key = f"POST {path}"
        if key in responses:
            resp = MagicMock()
            data = responses[key]
            resp.status_code = data.get("_status", 200)
            resp.json.return_value = data
            body = data.get("_body", "")
            resp.text = body
            headers = data.get("_headers", {"content-type": "application/json"})
            mock_headers = MagicMock()
            mock_headers.get = lambda k, d=None: headers.get(k, d)
            resp.headers = mock_headers
            return resp
        resp = MagicMock()
        resp.status_code = 404
        resp.text = ""
        mock_headers = MagicMock()
        mock_headers.get = lambda k, d=None: "application/json"
        resp.headers = mock_headers
        return resp

    client.get = _get
    client.post = _post
    return client


def _health_response(**kw):
    return {"ok": True, "runner": "whooshd", "version": "0.1.0",
            "status": "ready", "model_lifecycle": kw.get("lifecycle", "ready"),
            "active_model": None, "queue_depth": 0, "active_jobs": 0,
            "memory": {"pressure": "normal", "total_gb": 32, "used_gb": 4, "available_gb": 28}}


def _health_runtime_response(session_id="abc123", runtimes=None, **kw):
    if runtimes is None:
        runtimes = {"llama_cpp": {"kind": "llama_cpp", "enabled": True,
                     "state": "ready", "active_model": "test.gguf",
                     "configured_model": "test.gguf", "detail": "ready"}}
    return {"status": "ok", "runtimes": runtimes,
            "session": {"pid": 999, "session_id": session_id,
                        "started_at": "2026-01-01T00:00:00Z",
                        "registered_runtime_kinds": list(runtimes.keys())}}


def _models_response(models=None):
    if models is None:
        models = ["qwen2.5-0.5b-gguf", "llama-3.2-3b-mlx"]
    return {"object": "list", "data": [{"id": m, "object": "model",
            "created": 1, "owned_by": "whooshd"} for m in models]}


def _tags_response(models=None):
    if models is None:
        models = ["qwen2.5-0.5b-gguf"]
    return {"models": [{"name": m, "model": m,
            "modified_at": "", "size": 1} for m in models]}


def _stream_response(content: str = "Hello!", chunks: int = 2, has_done: bool = True):
    lines = []
    if chunks > 0:
        lines.append(
            f'data: {{"id":"c1","object":"chat.completion.chunk","created":1,'
            f'"model":"m","choices":[{{"index":0,"delta":{{"role":"assistant"}},"finish_reason":null}}]}}'
        )
    for i in range(chunks - 1 if chunks > 1 else 0):
        lines.append(
            f'data: {{"id":"c1","object":"chat.completion.chunk","created":1,'
            f'"model":"m","choices":[{{"index":0,"delta":{{"content":"{content}"}},"finish_reason":null}}]}}'
        )
    if has_done:
        lines.append("data: [DONE]")
    body = "\n".join(lines)
    return {
        "_status": 200,
        "_body": body,
        "_headers": {"content-type": "text/event-stream"},
    }


# ── SSE parser tests ────────────────────────────────────────────────────────


class TestParseCodexifySSE:
    def test_valid_stream(self):
        body = 'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\ndata: [DONE]\n'
        text, chunks, has_done = parse_codexify_sse(body)
        assert text == "Hi"
        assert chunks == 1
        assert has_done is True

    def test_missing_done(self):
        body = 'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n'
        text, chunks, has_done = parse_codexify_sse(body)
        assert text == "Hi"
        assert has_done is False

    def test_empty_stream(self):
        text, chunks, has_done = parse_codexify_sse("")
        assert text == ""
        assert chunks == 0
        assert has_done is False

    def test_malformed_json_skipped(self):
        body = 'data: {bad json}\ndata: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\ndata: [DONE]\n'
        text, chunks, has_done = parse_codexify_sse(body)
        assert text == "ok"
        assert chunks == 1


# ── Health checks ───────────────────────────────────────────────────────────


class TestHealthChecks:
    async def test_health_pass(self):
        client = _mock_client({"GET /health": _health_response()})
        c = await check_health(client)
        assert c.status == Status.PASS

    async def test_health_fail(self):
        client = _mock_client({"GET /health": {"ok": False}})
        c = await check_health(client)
        assert c.status == Status.FAIL

    async def test_health_runtime_stable(self):
        client = _mock_client({
            "GET /health/runtime": _health_runtime_response(session_id="abc123")
        })
        c, sid, kind = await check_health_runtime_stable(client)
        assert c.status == Status.PASS
        assert sid == "abc123"

    async def test_health_runtime_expect_runtime_pass(self):
        client = _mock_client({
            "GET /health/runtime": _health_runtime_response(
                session_id="abc",
                runtimes={"llama_cpp": {"kind": "llama_cpp", "enabled": True,
                           "state": "ready", "active_model": "t.gguf",
                           "configured_model": "t.gguf", "detail": "ok"}}
            )
        })
        c, sid, kind = await check_health_runtime_stable(client, expect_runtime="llama_cpp")
        assert c.status == Status.PASS

    async def test_health_runtime_expect_runtime_fail(self):
        client = _mock_client({
            "GET /health/runtime": _health_runtime_response(
                runtimes={"mlx_vlm": {"kind": "mlx_vlm", "enabled": True,
                         "state": "ready", "active_model": "v", "configured_model": "v", "detail": "ok"}}
            )
        })
        c, sid, kind = await check_health_runtime_stable(client, expect_runtime="llama_cpp")
        assert c.status == Status.FAIL
        assert "llama_cpp" in c.detail

    async def test_health_runtime_unstable_session(self):
        """Session ID changes between calls → FAIL."""
        client = MagicMock()
        client.timeout = 30.0
        call_count = [0]

        async def _get(path):
            call_count[0] += 1
            sid = f"session_{call_count[0]}"
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _health_runtime_response(session_id=sid)
            return resp

        client.get = _get
        c, sid, kind = await check_health_runtime_stable(client)
        assert c.status == Status.FAIL
        assert "unstable" in c.detail


# ── Model discovery ─────────────────────────────────────────────────────────


class TestModelDiscovery:
    async def test_model_found(self):
        client = _mock_client({"GET /v1/models": _models_response(["my-model"])})
        c = await check_models(client, "my-model")
        assert c.status == Status.PASS

    async def test_model_not_found(self):
        client = _mock_client({"GET /v1/models": _models_response(["other"])})
        c = await check_models(client, "my-model")
        assert c.status == Status.FAIL

    async def test_tag_found(self):
        client = _mock_client({"GET /api/tags": _tags_response(["my-model"])})
        c = await check_tags(client, "my-model")
        assert c.status == Status.PASS

    async def test_tag_not_found(self):
        client = _mock_client({"GET /api/tags": _tags_response(["other"])})
        c = await check_tags(client, "my-model")
        assert c.status == Status.FAIL


# ── Streaming chat ──────────────────────────────────────────────────────────


class TestStreamingChat:
    async def test_streaming_pass(self):
        client = _mock_client({
            "POST /v1/chat/completions": _stream_response("Hello world!", chunks=2, has_done=True)
        })
        c = await check_streaming_chat(client, "m", [{"role": "user", "content": "Hi"}])
        assert c.status == Status.PASS

    async def test_streaming_missing_done_fails(self):
        client = _mock_client({
            "POST /v1/chat/completions": _stream_response("Hi", has_done=False)
        })
        c = await check_streaming_chat(client, "m", [{"role": "user", "content": "Hi"}])
        assert c.status == Status.FAIL
        assert "missing [DONE]" in c.detail

    async def test_streaming_no_content_fails(self):
        client = _mock_client({
            "POST /v1/chat/completions": _stream_response("", chunks=0, has_done=True)
        })
        c = await check_streaming_chat(client, "m", [{"role": "user", "content": "Hi"}])
        assert c.status == Status.FAIL
        assert "no visible text" in c.detail or "no chunks" in c.detail


# ── Vision smoke ────────────────────────────────────────────────────────────


class TestVisionSmoke:
    def test_build_image_messages(self):
        import tempfile
        # Create a tiny valid PNG.
        import struct, zlib
        def _tiny_png():
            w, h = 1, 1
            def ch(t, d):
                c = t + d
                return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
            raw = b'\x00' + struct.pack('BBB', 255, 0, 0)
            return b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b'')

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(_tiny_png())
            f.flush()
            msgs = _build_image_messages(f.name, "Describe this.")
            os.unlink(f.name)

        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "base64" in content[1]["image_url"]["url"]
        assert "Describe this." in content[0]["text"]


# ── Full smoke test ─────────────────────────────────────────────────────────


class TestFullSmoke:
    async def test_healthy_provider_passes(self):
        """A properly configured provider passes all smoke checks."""
        responses = {
            "GET /health": _health_response(),
            "GET /health/runtime": _health_runtime_response(
                session_id="abc",
                runtimes={"llama_cpp": {"kind": "llama_cpp", "enabled": True,
                           "state": "ready", "active_model": "t.gguf",
                           "configured_model": "t.gguf", "detail": "ok"}}
            ),
            "GET /ready": {"ready": True, "reason": None},
            "GET /v1/models": _models_response(["qwen2.5-0.5b-gguf"]),
            "GET /api/tags": _tags_response(["qwen2.5-0.5b-gguf"]),
            "POST /v1/chat/completions": _stream_response("Hi!", chunks=2, has_done=True),
        }
        client = _mock_client(responses)

        checks, exit_code = await run_smoke(
            "http://127.0.0.1:8000", "qwen2.5-0.5b-gguf",
            expect_runtime="llama_cpp", _client=client,
        )
        assert exit_code == EXIT_OK
        assert all(c.status == Status.PASS for c in checks)

    async def test_missing_model_fails(self):
        """Expected model not in /v1/models → FAIL."""
        responses = {
            "GET /health": _health_response(),
            "GET /health/runtime": _health_runtime_response(),
            "GET /ready": {"ready": True, "reason": None},
            "GET /v1/models": _models_response(["other-model"]),
            "GET /api/tags": _tags_response(["other-model"]),
            "POST /v1/chat/completions": _stream_response(),
        }
        client = _mock_client(responses)

        checks, exit_code = await run_smoke("http://127.0.0.1:8000", "my-model", _client=client)
        assert exit_code == EXIT_FAIL
        assert any(c.status == Status.FAIL for c in checks)

    async def test_wrong_runtime_fails(self):
        """Expected runtime not found → FAIL."""
        responses = {
            "GET /health": _health_response(),
            "GET /health/runtime": _health_runtime_response(
                runtimes={"mlx_vlm": {"kind": "mlx_vlm", "enabled": True,
                         "state": "ready", "active_model": "v", "configured_model": "v", "detail": "ok"}}
            ),
            "GET /ready": {"ready": True},
            "GET /v1/models": _models_response(["v"]),
            "GET /api/tags": _tags_response(["v"]),
            "POST /v1/chat/completions": _stream_response(),
        }
        client = _mock_client(responses)

        checks, exit_code = await run_smoke(
            "http://127.0.0.1:8000", "v", expect_runtime="llama_cpp", _client=client,
        )
        assert exit_code == EXIT_FAIL

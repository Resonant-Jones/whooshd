"""Tests for POST /v1/chat/completions with stream=true — SSE stub."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_STREAM_REQUEST = {
    "model": "qwen2.5-1.5b-instruct-mlx",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": True,
}


def _parse_sse_lines(text: str) -> list[str]:
    """Extract data payloads from an SSE response body."""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            lines.append(line[6:])  # strip "data: " prefix
    return lines


# ── HTTP-level contract ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_returns_200(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_streaming_content_type_is_event_stream(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    ct = resp.headers.get("content-type", "")
    assert "text/event-stream" in ct


@pytest.mark.asyncio
async def test_streaming_cache_control_header(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    assert resp.headers.get("cache-control") == "no-cache"


@pytest.mark.asyncio
async def test_streaming_body_is_not_empty(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    body = resp.text
    assert len(body) > 0
    assert body.startswith("data: ")


# ── SSE framing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_line_starts_with_data_prefix(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    for line in resp.text.splitlines():
        if line.strip():
            assert line.startswith("data: "), f"Line missing data prefix: {line!r}"


@pytest.mark.asyncio
async def test_stream_terminates_with_done(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    assert payloads[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_no_empty_data_lines(client):
    """Every meaningful data: line carries a payload or [DONE]."""
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    for payload in payloads:
        assert len(payload) > 0


# ── Chunk shape ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chunks_are_valid_json(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    for payload in payloads[:-1]:  # skip [DONE]
        parsed = json.loads(payload)
        assert isinstance(parsed, dict)


@pytest.mark.asyncio
async def test_chunks_have_required_fields(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    for payload in payloads[:-1]:
        chunk = json.loads(payload)
        assert "id" in chunk
        assert chunk["object"] == "chat.completion.chunk"
        assert isinstance(chunk["created"], int)
        assert chunk["created"] > 0
        assert "model" in chunk
        assert "choices" in chunk
        assert isinstance(chunk["choices"], list)
        assert len(chunk["choices"]) == 1


@pytest.mark.asyncio
async def test_chunk_ids_are_consistent(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    chunk_ids = set()
    for payload in payloads[:-1]:
        chunk_ids.add(json.loads(payload)["id"])
    assert len(chunk_ids) == 1  # all chunks share the same completion id


@pytest.mark.asyncio
async def test_first_chunk_has_role_delta_only(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    first = json.loads(payloads[0])
    delta = first["choices"][0]["delta"]
    assert delta.get("role") == "assistant"
    assert delta.get("content") is None
    assert first["choices"][0].get("finish_reason") is None


@pytest.mark.asyncio
async def test_content_chunks_have_no_role(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    # Content chunks: skip role marker (idx 0), final empty delta, and [DONE].
    content_payloads = payloads[1:-2]
    for payload in content_payloads:
        chunk = json.loads(payload)
        delta = chunk["choices"][0]["delta"]
        assert delta.get("role") is None
        assert delta.get("content") is not None
        assert len(delta["content"]) > 0


@pytest.mark.asyncio
async def test_content_chunks_form_coherent_text(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    # Collect content from word-token chunks (skip role marker, final empty delta, [DONE]).
    tokens: list[str] = []
    for payload in payloads[1:-2]:
        delta = json.loads(payload)["choices"][0]["delta"]
        tokens.append(delta["content"])
    assembled = "".join(tokens)
    assert "streaming" in assembled or "online" in assembled
    assert len(assembled) > 0


@pytest.mark.asyncio
async def test_final_chunk_has_empty_delta_and_stop_reason(client):
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    # The chunk before [DONE] is the final chunk.
    final = json.loads(payloads[-2])
    delta = final["choices"][0]["delta"]
    # Empty delta: no role, no content.
    assert delta.get("role") is None
    assert delta.get("content") is None
    assert final["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_no_reasoning_or_internal_fields(client):
    """No fields from o1-style reasoning or internal-only metadata leak."""
    resp = await client.post("/v1/chat/completions", json=_STREAM_REQUEST)
    payloads = _parse_sse_lines(resp.text)
    permitted_top = {"id", "object", "created", "model", "choices"}
    permitted_delta = {"role", "content"}
    for payload in payloads[:-1]:
        chunk = json.loads(payload)
        extra_top = set(chunk.keys()) - permitted_top
        assert not extra_top, f"Unexpected top-level fields: {extra_top}"
        for choice in chunk["choices"]:
            delta = choice.get("delta", {})
            extra_delta = set(delta.keys()) - permitted_delta
            assert not extra_delta, f"Unexpected delta fields: {extra_delta}"


# ── Validation still works ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_empty_messages_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_STREAM_REQUEST, "messages": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_streaming_missing_model_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={k: v for k, v in _STREAM_REQUEST.items() if k != "model"},
    )
    assert resp.status_code == 422


# ── Non-streaming still works ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_unchanged(client):
    """stream=false must still produce the familiar JSON response."""
    resp = await client.post(
        "/v1/chat/completions",
        json={**_STREAM_REQUEST, "stream": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert "stub response" in body["choices"][0]["message"]["content"]


# ── Adapter contract (no HTTP) ──────────────────────────────────────────────


class TestStreamingAdapterContract:
    async def test_stub_stream_yields_chunks(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        chunks = [c async for c in adapter.chat_completion_stream(req)]
        assert len(chunks) >= 3  # role + at least 1 content + finish

        # First is role delta only.
        assert chunks[0].choices[0].delta.role == "assistant"
        assert chunks[0].choices[0].delta.content is None

        # Final is finish_reason only.
        assert chunks[-1].choices[0].delta.role is None
        assert chunks[-1].choices[0].delta.content is None
        assert chunks[-1].choices[0].finish_reason == "stop"

        # Content in the middle.
        middle_words = [
            c.choices[0].delta.content
            for c in chunks[1:-1]
        ]
        assert all(w is not None for w in middle_words)

    async def test_stub_stream_ids_are_consistent(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        chunk_ids = {
            c.id
            async for c in adapter.chat_completion_stream(req)
        }
        assert len(chunk_ids) == 1

    async def test_stub_stream_to_sse(self):
        """The to_sse() helper emits correct SSE framing."""
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        async for chunk in adapter.chat_completion_stream(req):
            sse = chunk.to_sse()
            assert sse.startswith("data: ")
            assert sse.endswith("\n\n")
            # The JSON between "data: " and "\n\n" must parse.
            json_str = sse[6:-2]
            parsed = json.loads(json_str)
            assert parsed["id"] == chunk.id

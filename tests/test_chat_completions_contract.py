"""Tests for POST /v1/chat/completions — OpenAI-compatible stub."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_MINIMAL_REQUEST = {
    "model": "qwen2.5-1.5b-instruct-mlx",
    "messages": [{"role": "user", "content": "Hello"}],
}


# ── Happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_completions_returns_200(client):
    resp = await client.post("/v1/chat/completions", json=_MINIMAL_REQUEST)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_chat_completions_body_shape(client):
    resp = await client.post("/v1/chat/completions", json=_MINIMAL_REQUEST)
    body = resp.json()

    assert body["object"] == "chat.completion"
    assert isinstance(body["id"], str)
    assert body["id"].startswith("chatcmpl-stub-")
    assert isinstance(body["created"], int)
    assert body["created"] > 0
    assert body["model"] == "qwen2.5-1.5b-instruct-mlx"

    # Choices
    assert isinstance(body["choices"], list)
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert "stub response" in choice["message"]["content"]
    assert "chat completion contract is online" in choice["message"]["content"]

    # Usage
    assert "usage" in body
    usage = body["usage"]
    assert usage["prompt_tokens"] is not None
    assert usage["completion_tokens"] is not None
    assert usage["total_tokens"] is not None
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


@pytest.mark.asyncio
async def test_chat_completions_stub_text_is_deterministic(client):
    resp = await client.post("/v1/chat/completions", json=_MINIMAL_REQUEST)
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == (
        "Whoosh'd stub response: chat completion contract is online."
    )


@pytest.mark.asyncio
async def test_chat_completions_with_system_message(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Prompt tokens should account for both messages.
    assert body["usage"]["prompt_tokens"] >= 2


@pytest.mark.asyncio
async def test_chat_completions_multiple_user_messages(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Response"},
                {"role": "user", "content": "Second"},
            ],
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_chat_completions_different_model(client):
    """The stub adapter echoes whatever model ID you send."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "any-model-id", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "any-model-id"


# ── Streaming rejection ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_true_returns_501(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )
    assert resp.status_code == 501
    body = resp.json()
    assert body["code"] == "INTERNAL"
    assert "stream" in body["message"].lower()


# ── Validation errors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_messages_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen2.5-1.5b-instruct-mlx", "messages": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_messages_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "qwen2.5-1.5b-instruct-mlx"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_model_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_message_content_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": ""}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_role_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "invalid_role", "content": "Hello"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_temperature_below_range_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "temperature": -0.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_temperature_above_range_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "temperature": 2.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_tokens_zero_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "max_tokens": 0},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_tokens_above_limit_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "max_tokens": 99999},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_top_p_below_range_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "top_p": -0.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_top_p_above_range_returns_422(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={**_MINIMAL_REQUEST, "top_p": 1.1},
    )
    assert resp.status_code == 422


# ── Contract model tests (no HTTP) ─────────────────────────────────────────


class TestChatCompletionRequestModel:
    def test_minimal_request(self):
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        assert req.model == "test-model"
        assert len(req.messages) == 1
        assert req.temperature == 0.7
        assert req.max_tokens == 256
        assert req.stream is False

    def test_empty_messages_rejected(self):
        from whooshd.contracts import ChatCompletionRequest

        with pytest.raises(Exception):
            ChatCompletionRequest(model="m", messages=[])

    def test_empty_message_content_rejected(self):
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        with pytest.raises(Exception):
            ChatCompletionRequest(
                model="m",
                messages=[ChatMessage(role="user", content="")],
            )


class TestChatCompletionResponseModel:
    def test_minimal_response(self):
        from whooshd.contracts import (
            ChatCompletionChoice,
            ChatCompletionResponse,
            ChatCompletionUsage,
            ChatMessage,
        )

        resp = ChatCompletionResponse(
            id="chatcmpl-123",
            created=1700000000,
            model="test-model",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content="Hello back"),
                    finish_reason="stop",
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
            ),
        )
        assert resp.object == "chat.completion"
        assert resp.id == "chatcmpl-123"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.role == "assistant"

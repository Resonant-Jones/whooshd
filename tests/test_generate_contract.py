"""Tests for POST /v1/generate endpoint and generate contracts."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import GenerateRequest, GenerateResponse


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Contract validation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_returns_200(client):
    resp = await client.post("/v1/generate", json={"prompt": "Hello"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_generate_body_shape(client):
    resp = await client.post("/v1/generate", json={"prompt": "Hello"})
    body = resp.json()

    assert body["ok"] is True
    assert isinstance(body["request_id"], str)
    assert len(body["request_id"]) > 0
    assert isinstance(body["text"], str)
    assert len(body["text"]) > 0
    assert body["finish_reason"] == "stop"

    # TokenUsage
    assert "usage" in body
    usage = body["usage"]
    assert usage["prompt_tokens"] is not None
    assert usage["completion_tokens"] is not None
    assert usage["total_tokens"] is not None

    # ResponseRuntimeInfo
    assert "runtime" in body
    runtime = body["runtime"]
    assert runtime["adapter"] == "stub"
    assert runtime["queued"] is False
    assert isinstance(runtime["elapsed_ms"], (int, float))
    assert runtime["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_generate_returns_stub_text(client):
    resp = await client.post("/v1/generate", json={"prompt": "Hello, world!"})
    body = resp.json()

    # Stub adapter echoes the prompt
    assert "stub response" in body["text"]
    assert "echo:" in body["text"]
    assert "Hello, world!" in body["text"]


@pytest.mark.asyncio
async def test_generate_request_id_passthrough(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "request_id": "my-req-42"},
    )
    body = resp.json()
    assert body["request_id"] == "my-req-42"


def test_generate_request_id_is_correlation_only():
    req = GenerateRequest(prompt="test", request_id="my-req-42")
    assert req.client_request_id == "my-req-42"
    assert req.request_id == "my-req-42"


@pytest.mark.asyncio
async def test_generate_model_id_passthrough(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "model_id": "qwen2.5-1.5b-instruct-mlx"},
    )
    body = resp.json()
    assert body["model_id"] == "qwen2.5-1.5b-instruct-mlx"


@pytest.mark.asyncio
async def test_generate_default_model_id_when_omitted(client):
    resp = await client.post("/v1/generate", json={"prompt": "No model"})
    body = resp.json()
    assert body["model_id"] == "stub-model"


# ── Validation errors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_prompt_returns_422(client):
    resp = await client.post("/v1/generate", json={"prompt": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "invalid_request"
    assert "validation" in body["message"].lower()


@pytest.mark.asyncio
async def test_missing_prompt_returns_422(client):
    resp = await client.post("/v1/generate", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_max_tokens_too_low_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "max_tokens": 0},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_tokens_too_high_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "max_tokens": 20000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_temperature_below_range_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "temperature": -0.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_temperature_above_range_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "temperature": 2.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_top_p_below_range_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "top_p": -0.1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_top_p_above_range_returns_422(client):
    resp = await client.post(
        "/v1/generate",
        json={"prompt": "Test", "top_p": 1.1},
    )
    assert resp.status_code == 422


# ── Contract model tests (no HTTP) ─────────────────────────────────────────


class TestGenerateRequestModel:
    def test_valid_request(self):
        req = GenerateRequest(prompt="Hello")
        assert req.prompt == "Hello"
        assert req.max_tokens == 256
        assert req.temperature == 0.7
        assert req.top_p == 0.95
        assert req.model_id is None
        assert req.stop is None
        assert req.request_id is None

    def test_full_request(self):
        req = GenerateRequest(
            prompt="Full test",
            model_id="my-model",
            max_tokens=512,
            temperature=0.5,
            top_p=0.8,
            stop=["\n", "END"],
            request_id="req-full",
        )
        assert req.max_tokens == 512
        assert req.temperature == 0.5
        assert req.top_p == 0.8
        assert req.stop == ["\n", "END"]
        assert req.request_id == "req-full"

    def test_empty_prompt_rejected(self):
        with pytest.raises(Exception):  # Pydantic ValidationError
            GenerateRequest(prompt="")

    def test_max_tokens_zero_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(prompt="x", max_tokens=0)

    def test_temperature_negative_rejected(self):
        with pytest.raises(Exception):
            GenerateRequest(prompt="x", temperature=-1.0)


class TestGenerateResponseModel:
    def test_minimal_response(self):
        from whooshd.contracts import ResponseRuntimeInfo, TokenUsage

        resp = GenerateResponse(
            request_id="abc",
            text="hello",
            finish_reason="stop",
            runtime=ResponseRuntimeInfo(adapter="stub", elapsed_ms=1.0),
        )
        assert resp.ok is True
        assert resp.request_id == "abc"
        assert resp.text == "hello"
        assert resp.finish_reason == "stop"
        assert resp.usage.prompt_tokens is None
        assert resp.runtime.adapter == "stub"

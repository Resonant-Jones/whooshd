"""Tests for GET /v1/models (OpenAI-style) and GET /api/tags (Ollama-style)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── /v1/models (OpenAI format) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_models_returns_200(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openai_models_body_shape(client):
    resp = await client.get("/v1/models")
    body = resp.json()

    assert body["object"] == "list"
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1


@pytest.mark.asyncio
async def test_openai_models_entry_shape(client):
    resp = await client.get("/v1/models")
    entry = resp.json()["data"][0]

    assert entry["object"] == "model"
    assert isinstance(entry["id"], str)
    assert len(entry["id"]) > 0
    assert isinstance(entry["created"], int)
    assert entry["created"] > 0
    assert entry["owned_by"] == "whooshd"


@pytest.mark.asyncio
async def test_openai_models_stub_model_present(client):
    resp = await client.get("/v1/models")
    model_ids = [m["id"] for m in resp.json()["data"]]

    assert "stub-model" in model_ids


# ── /api/tags (Ollama format) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_tags_returns_200(client):
    resp = await client.get("/api/tags")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ollama_tags_body_shape(client):
    resp = await client.get("/api/tags")
    body = resp.json()

    assert isinstance(body["models"], list)
    assert len(body["models"]) >= 1


@pytest.mark.asyncio
async def test_ollama_tags_entry_shape(client):
    resp = await client.get("/api/tags")
    entry = resp.json()["models"][0]

    assert isinstance(entry["name"], str)
    assert len(entry["name"]) > 0
    assert isinstance(entry["modified_at"], str)
    assert isinstance(entry["size"], int)
    assert entry["size"] > 0


@pytest.mark.asyncio
async def test_ollama_tags_stub_model_present(client):
    resp = await client.get("/api/tags")
    tag_names = [m["name"] for m in resp.json()["models"]]

    assert "stub-model" in tag_names


# ── Consistency between aliases ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_ids_consistent_across_aliases(client):
    """Both /v1/models and /api/tags describe the same model inventory."""
    openai_resp = await client.get("/v1/models")
    ollama_resp = await client.get("/api/tags")

    openai_ids = {m["id"] for m in openai_resp.json()["data"]}
    ollama_base_names = {m["name"].split(":")[0] for m in ollama_resp.json()["models"]}

    assert openai_ids == ollama_base_names

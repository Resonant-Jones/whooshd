"""Tests for GET /runtime and GET /models."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── /runtime ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_returns_200(client):
    resp = await client.get("/runtime")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_runtime_body_shape(client):
    resp = await client.get("/runtime")
    body = resp.json()

    assert "memory" in body
    assert "loaded_models" in body
    assert isinstance(body["loaded_models"], list)
    assert "concurrency" in body
    assert "uptime_seconds" in body
    assert body["uptime_seconds"] >= 0


@pytest.mark.asyncio
async def test_runtime_concurrency_fields(client):
    resp = await client.get("/runtime")
    cc = resp.json()["concurrency"]

    assert cc["max_active_jobs"] >= 0
    assert cc["estimated_safe_concurrency"] >= 0
    assert cc["queue_capacity"] >= 0


@pytest.mark.asyncio
async def test_runtime_uptime_increases(client):
    resp1 = await client.get("/runtime")
    t1 = resp1.json()["uptime_seconds"]

    resp2 = await client.get("/runtime")
    t2 = resp2.json()["uptime_seconds"]

    assert t2 >= t1


# ── /models ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_models_returns_200(client):
    resp = await client.get("/models")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_models_body_shape(client):
    resp = await client.get("/models")
    body = resp.json()

    assert "models" in body
    assert isinstance(body["models"], list)
    assert len(body["models"]) >= 0


@pytest.mark.asyncio
async def test_models_entries_have_required_fields(client):
    resp = await client.get("/models")
    models = resp.json()["models"]

    required = {"id", "loaded", "capabilities", "max_concurrent_jobs", "context_window", "memory_class"}
    for m in models:
        missing = required - set(m.keys())
        assert not missing, f"Model {m.get('id', '?')} missing fields: {missing}"


@pytest.mark.asyncio
async def test_stubbed_model_present(client):
    resp = await client.get("/models")
    model_ids = [m["id"] for m in resp.json()["models"]]

    assert "qwen2.5-1.5b-instruct-mlx" in model_ids

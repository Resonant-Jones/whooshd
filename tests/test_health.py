"""Tests for GET /health."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_body_shape(client):
    resp = await client.get("/health")
    body = resp.json()

    assert body["ok"] is True
    assert body["runner"] == "whooshd"
    assert isinstance(body["version"], str)
    assert body["version"] != ""
    assert body["active_model"] is None
    assert body["queue_depth"] == 0
    assert body["active_jobs"] == 0
    assert "memory" in body
    assert body["memory"]["pressure"] == "normal"


@pytest.mark.asyncio
async def test_health_memory_fields(client):
    resp = await client.get("/health")
    mem = resp.json()["memory"]

    assert mem["total_gb"] > 0
    assert mem["used_gb"] >= 0
    assert mem["available_gb"] >= 0

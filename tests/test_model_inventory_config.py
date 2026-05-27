"""Config-driven model inventory tests.

These tests prove the advertised inventory tracks the configured model
id, works before warmup, and does not require mlx-lm to be imported.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.compat.codexify_probe import CodexifyProbe

MLX_MODEL_ID = "mlx-community/Llama-3.2-3B-Instruct-4bit"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_stub_backend_advertises_stub_model(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_ADAPTER", "stub")

    openai_resp = await client.get("/v1/models")
    ollama_resp = await client.get("/api/tags")

    assert openai_resp.status_code == 200
    assert ollama_resp.status_code == 200
    assert [m["id"] for m in openai_resp.json()["data"]] == ["stub-model"]
    assert [m["name"] for m in ollama_resp.json()["models"]] == ["stub-model"]


@pytest.mark.asyncio
async def test_mlx_inventory_advertises_configured_model_before_warmup(
    client, monkeypatch
):
    monkeypatch.setenv("WHOOSHD_ADAPTER", "mlx")
    monkeypatch.setenv("WHOOSHD_MLX_MODEL", MLX_MODEL_ID)
    monkeypatch.delitem(sys.modules, "mlx_lm", raising=False)

    with patch(
        "whooshd.adapters.mlx.MLXInferenceAdapter._import_mlx_lm",
        side_effect=AssertionError(
            "inventory should not import mlx_lm before warmup"
        ),
    ):
        openai_resp = await client.get("/v1/models")
        ollama_resp = await client.get("/api/tags")

    assert openai_resp.status_code == 200
    assert ollama_resp.status_code == 200
    assert [m["id"] for m in openai_resp.json()["data"]] == [MLX_MODEL_ID]
    assert [m["name"] for m in ollama_resp.json()["models"]] == [MLX_MODEL_ID]


@pytest.mark.asyncio
async def test_codexify_probe_sees_consistent_inventory(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_ADAPTER", "mlx")
    monkeypatch.setenv("WHOOSHD_MLX_MODEL", MLX_MODEL_ID)

    probe = CodexifyProbe(client)
    openai = await probe.probe_models_openai()
    ollama = await probe.probe_models_ollama()

    assert openai.model_ids == [MLX_MODEL_ID]
    assert ollama.model_ids == [MLX_MODEL_ID]
    assert {model.split(":")[0] for model in ollama.model_ids} == set(
        openai.model_ids
    )

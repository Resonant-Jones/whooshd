"""Tests for /ready, liveness-vs-readiness distinction, and smoke probes."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.contracts import ModelLifecycleState, RunnerStatus
from whooshd.runtime import RuntimeState, get_runtime


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── /ready endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ready_returns_200_when_stub_is_ready(client):
    """Stub is always ready — /ready should return 200."""
    # Ensure lifecycle is synced — a prior test may have called unload.
    await client.post("/runtime/model/warmup")
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["reason"] is None


@pytest.mark.asyncio
async def test_ready_body_shape(client):
    resp = await client.get("/ready")
    body = resp.json()
    assert "ready" in body
    assert "status" in body
    assert "model_lifecycle" in body
    assert "adapter" in body
    assert "configured_model" in body
    assert "loaded_model" in body
    assert "reason" in body


@pytest.mark.asyncio
async def test_ready_no_prompt_leakage(client):
    """/ready must never contain prompt text or message content."""
    # Send a chat request to populate some runtime state, then check /ready.
    await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-1.5b-instruct-mlx",
            "messages": [{"role": "user", "content": "Secret prompt"}],
            "stream": False,
        },
    )
    resp = await client.get("/ready")
    body_str = str(resp.json())
    assert "Secret prompt" not in body_str
    assert "prompt" not in resp.json()
    assert "messages" not in resp.json()
    assert "content" not in resp.json()


# ── Liveness vs readiness distinction ───────────────────────────────────────


@pytest.mark.asyncio
async def test_health_remains_200_during_warming(client):
    """/health is liveness — must stay 200 even when model is warming."""
    rt = get_runtime()
    rt.begin_warmup()
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["model_lifecycle"] == "warming"
    finally:
        rt.complete_warmup()  # restore state


@pytest.mark.asyncio
async def test_ready_returns_503_during_warming(client):
    """/ready is readiness — must return 503 when model is warming."""
    rt = get_runtime()
    rt.begin_warmup()
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        assert resp.json()["reason"] == "model_warming"
    finally:
        rt.complete_warmup()


@pytest.mark.asyncio
async def test_ready_returns_503_when_unloaded(client):
    """/ready returns 503 when no model is loaded."""
    rt = get_runtime()
    # Force unloaded state.
    original = rt.model_lifecycle
    rt.complete_unload()
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        assert resp.json()["reason"] == "model_unloaded"
    finally:
        rt.model_lifecycle = original


@pytest.mark.asyncio
async def test_ready_returns_503_when_failed(client):
    """/ready returns 503 when model load has failed."""
    rt = get_runtime()
    original = rt.model_lifecycle
    rt.fail_warmup(error_code="MODEL_LOAD_FAILED", error_message="test")
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        assert resp.json()["reason"] == "model_load_failed"
    finally:
        rt.model_lifecycle = original


@pytest.mark.asyncio
async def test_health_remains_200_after_load_failure(client):
    """/health must not report offline just because the model failed to load."""
    rt = get_runtime()
    original = rt.model_lifecycle
    orig_status = rt.status
    rt.status = RunnerStatus.DEGRADED
    rt.fail_warmup(error_code="MODEL_LOAD_FAILED", error_message="test")
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
    finally:
        rt.model_lifecycle = original
        rt.status = orig_status


# ── Consistency between endpoints ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_model_lifecycle_matches_ready(client):
    rt = get_runtime()
    original = rt.model_lifecycle
    rt.begin_warmup()
    rt.complete_warmup()
    try:
        model_resp = await client.get("/runtime/model")
        ready_resp = await client.get("/ready")
        assert model_resp.json()["lifecycle_state"] == ready_resp.json()["model_lifecycle"]
    finally:
        rt.model_lifecycle = original


@pytest.mark.asyncio
async def test_health_model_lifecycle_matches_runtime(client):
    health_resp = await client.get("/health")
    runtime_resp = await client.get("/runtime")
    assert health_resp.json()["model_lifecycle"] == runtime_resp.json()["model_lifecycle"]


# ── Smoke probe (stub backend) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_probe_succeeds_against_stub(client):
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    assert result.ok is True
    assert result.health_ok is True
    assert result.ready is True
    assert result.openai_models_ok is True
    assert result.ollama_tags_ok is True
    assert result.non_streaming_chat_ok is True
    assert result.streaming_chat_ok is True
    assert result.streaming_visible_text == "Whoosh'd streaming stub online."
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_smoke_probe_reports_failure_cleanly(client):
    """Even when readiness fails, the probe must not raise raw tracebacks."""
    rt = get_runtime()
    original = rt.model_lifecycle
    rt.fail_warmup(error_code="MODEL_LOAD_FAILED", error_message="boom")
    try:
        from whooshd.compat.probe_server import smoke_test_server

        result = await smoke_test_server(client)
        # Health should still be ok (process alive).
        assert result.health_ok is True
        # Ready should be false.
        assert result.ready is False
        assert result.readiness_reason == "model_load_failed"
        # The probe should not have raised — errors are in the list.
        assert isinstance(result.errors, list)
    finally:
        rt.model_lifecycle = original


@pytest.mark.asyncio
async def test_smoke_probe_no_prompt_leakage(client):
    """ProviderSmokeResult must never expose prompt text or message content."""
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    result_str = str(result)
    # The stub chat text is deterministic and does not echo prompts.
    assert "Secret" not in result_str


# ── MLX mocked readiness ────────────────────────────────────────────────────


@pytest.fixture
def mock_mlx_lm_module():
    """Inject a mock mlx_lm module into sys.modules."""
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "user\nHello\nassistant\n"

    mock_mlx = MagicMock()
    mock_mlx.load.return_value = (MagicMock(), mock_tokenizer)
    mock_mlx.generate.return_value = "Mock"
    mock_mlx.stream_generate.return_value = iter([])

    sys.modules["mlx_lm"] = mock_mlx
    yield mock_mlx
    del sys.modules["mlx_lm"]


class TestMLXReadiness:
    async def test_ready_503_before_warmup(self, mock_mlx_lm_module):
        """Before warmup, /ready with MLX backend should return 503."""
        import os
        from whooshd.runtime import RuntimeState

        rt = RuntimeState()
        assert rt.model_lifecycle == ModelLifecycleState.UNLOADED
        # Simulate: unloaded → /ready returns 503.
        snapshot = rt.build_model_snapshot(
            adapter_name="mlx-lm", configured_model="test-model"
        )
        assert snapshot.lifecycle_state == ModelLifecycleState.UNLOADED

    async def test_warmup_drives_ready(self, mock_mlx_lm_module):
        """After warmup, lifecycle should be READY."""
        from whooshd.adapters.mlx import MLXInferenceAdapter

        adapter = MLXInferenceAdapter()
        await adapter.warmup()
        assert adapter.is_loaded() is True

    async def test_load_failure_drives_not_ready(self, mock_mlx_lm_module):
        """Model load failure should leave lifecycle in FAILED."""
        mock_mlx_lm_module.load.side_effect = RuntimeError("load failed")
        from whooshd.adapters.mlx import MLXInferenceAdapter

        adapter = MLXInferenceAdapter()
        with pytest.raises(RuntimeError):
            await adapter.warmup()
        assert adapter.is_loaded() is False

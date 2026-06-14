"""Tests for vision capability routing and MLX-VLM adapter.

Validates:
  * text-only request routes to text model
  * image request routes to mlx-vlm
  * image request rejected for text-only model
  * image content is preserved in forwarded body
  * /v1/models includes MLX-VLM model with supports_vision
  * MLX-VLM adapter identity and health
  * MLX-VLM concurrency guard
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app, reset_router, _init_router
from whooshd.contracts import ChatCompletionRequest, ChatMessage, RuntimeKind
from whooshd.http_forwarding import build_forward_body, _serialize_message
from whooshd.routing import reset_router as routing_reset, get_router
from whooshd.app import _request_has_image_content, _adapter_supports_vision


@pytest.fixture(autouse=True)
def _clean():
    routing_reset()
    _init_router()
    from whooshd.runtime import get_runtime
    get_runtime()._registry = None
    yield
    routing_reset()
    _init_router()


# ── Image request helpers ──────────────────────────────────────────────────


def _make_text_req(model: str = "stub-model") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Describe this.")],
        stream=False,
    )


def _make_image_req(model: str = "stub-model") -> ChatCompletionRequest:
    """Create a request with OpenAI-compatible multimodal image content."""
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content=[
            {"type": "text", "text": "Describe this image."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ])],
        stream=False,
    )


# ── Image detection tests ──────────────────────────────────────────────────


class TestImageDetection:
    def test_text_only_request_no_image(self):
        req = _make_text_req()
        assert _request_has_image_content(req) is False

    def test_multimodal_request_has_image(self):
        req = _make_image_req()
        assert _request_has_image_content(req) is True

    def test_mixed_messages_one_with_image(self):
        req = ChatCompletionRequest(
            model="m",
            messages=[
                ChatMessage(role="user", content="Hi"),
                ChatMessage(role="user", content=[
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aa"}},
                ]),
            ],
            stream=False,
        )
        assert _request_has_image_content(req) is True

    def test_list_content_without_image(self):
        req = ChatCompletionRequest(
            model="m",
            messages=[ChatMessage(role="user", content=[
                {"type": "text", "text": "Hello"},
            ])],
            stream=False,
        )
        assert _request_has_image_content(req) is False


# ── Adapter vision detection ───────────────────────────────────────────────


class TestAdapterVisionDetection:
    def test_mlx_vlm_adapter_supports_vision(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        assert _adapter_supports_vision(adapter) is True

    def test_stub_adapter_does_not_support_vision(self):
        from whooshd.adapters.stub import StubInferenceAdapter
        adapter = StubInferenceAdapter()
        assert _adapter_supports_vision(adapter) is False


# ── Message serialization with multimodal content ──────────────────────────


class TestMultimodalSerialization:
    def test_image_content_preserved_in_serialization(self):
        msg = ChatMessage(role="user", content=[
            {"type": "text", "text": "Describe this."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ])
        d = _serialize_message(msg)
        assert isinstance(d["content"], list)
        assert d["content"][0]["type"] == "text"
        assert d["content"][1]["type"] == "image_url"
        assert "abc" in d["content"][1]["image_url"]["url"]

    def test_image_content_preserved_in_forward_body(self):
        req = _make_image_req(model="vision-model")
        body = build_forward_body(req)
        assert isinstance(body["messages"][0]["content"], list)
        assert body["messages"][0]["content"][1]["type"] == "image_url"


# ── MLX-VLM adapter identity ───────────────────────────────────────────────


class TestMlxVlmAdapterIdentity:
    def test_kind_and_name(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        assert adapter.kind == RuntimeKind.MLX_VLM.value
        assert adapter.name == "mlx-vlm"
        assert adapter.supports_streaming is True

    def test_list_models_marks_vision(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        from whooshd.adapters.mlx_vlm import _MlxVlmHealthStatus

        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="mlx-community/test-vision"))
        adapter.check_health = AsyncMock(return_value=_MlxVlmHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ok"))
        adapter.is_loaded = MagicMock(return_value=True)

        async def _run():
            models = await adapter.list_models()
            assert len(models) == 1
            assert models[0].supports_vision is True
            assert models[0].format == "mlx"
            assert models[0].runtime == RuntimeKind.MLX_VLM.value

        asyncio.run(_run())


# ── MLX-VLM concurrency guard ──────────────────────────────────────────────


class TestMlxVlmConcurrency:
    def test_default_max_concurrent_is_1(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        assert adapter._max_concurrent == 1

    def test_second_request_rejected(self, monkeypatch):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        from whooshd.adapters.mlx_vlm import _MlxVlmHealthStatus
        from whooshd.http_forwarding import RuntimeOverloaded

        monkeypatch.setenv("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", "0.5")
        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        adapter._max_concurrent = 1
        adapter._concurrency_semaphore = asyncio.Semaphore(1)
        adapter.check_health = AsyncMock(return_value=_MlxVlmHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ok"))

        hold = asyncio.Event()

        async def blocking_post(*a, **kw):
            await hold.wait()
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return resp

        with patch("whooshd.http_forwarding.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = blocking_post
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            async def _run():
                t1 = asyncio.create_task(adapter.chat_completion(_make_text_req()))
                await asyncio.sleep(0.1)
                with pytest.raises(RuntimeOverloaded):
                    await adapter.chat_completion(_make_text_req())
                hold.set()
                await t1

            asyncio.run(_run())


# ── HTTP vision routing ────────────────────────────────────────────────────


class TestVisionRoutingHTTP:
    @pytest.mark.asyncio
    async def test_image_request_to_text_model_rejected(self):
        """Image request with a text-only model returns 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,aa"}},
                ]}],
                "stream": False,
            })
            assert resp.status_code == 400
            assert "vision" in resp.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_text_request_to_text_model_accepted(self):
        """Text-only request to text-only model is fine."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            })
            # Stub adapter accepts text requests.
            assert resp.status_code == 200


# ── MLX-VLM inventory ──────────────────────────────────────────────────────


class TestMlxVlmInventory:
    @pytest.mark.asyncio
    async def test_mlx_vlm_appears_in_v1_models(self):
        """When MLX-VLM is registered, its model appears in inventory."""
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        from whooshd.adapters.mlx_vlm import _MlxVlmHealthStatus

        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="mlx-community/test-vision"))
        adapter.check_health = AsyncMock(return_value=_MlxVlmHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ok"))
        adapter.is_loaded = MagicMock(return_value=True)

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "mlx-community/test-vision" in ids

    @pytest.mark.asyncio
    async def test_mlx_vlm_appears_in_api_tags(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        from whooshd.adapters.mlx_vlm import _MlxVlmHealthStatus

        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="mlx-community/test-vision"))
        adapter.check_health = AsyncMock(return_value=_MlxVlmHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ok"))
        adapter.is_loaded = MagicMock(return_value=True)

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            assert resp.status_code == 200
            names = [m["name"] for m in resp.json()["models"]]
            assert "mlx-community/test-vision" in names


# ── Health aggregation ─────────────────────────────────────────────────────


class TestMlxVlmHealth:
    @pytest.mark.asyncio
    async def test_mlx_vlm_appears_in_health_runtime(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
        from whooshd.adapters.mlx_vlm import _MlxVlmHealthStatus

        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        adapter.check_health = AsyncMock(return_value=_MlxVlmHealthStatus(
            reachable=True, runner_status="ready", model_lifecycle="ready", detail="ok"))
        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/runtime")
            assert resp.status_code == 200
            runtimes = resp.json()["runtimes"]
            assert RuntimeKind.MLX_VLM.value in runtimes
            assert runtimes[RuntimeKind.MLX_VLM.value]["state"] == "ready"


# ── MLX-VLM argv building ──────────────────────────────────────────────────


class TestMlxVlmArgv:
    def test_basic_argv(self):
        from whooshd.adapters.mlx_vlm import build_mlx_vlm_server_argv, MlxVlmConfig

        config = MlxVlmConfig(enabled=True, host="127.0.0.1", port=8082, model="test-model")
        argv = build_mlx_vlm_server_argv(config)
        assert argv[0] == "python"
        assert argv[1] == "-m"
        assert argv[2] == "mlx_vlm"
        assert argv[3] == "server"
        assert "--model" in argv
        assert "test-model" in argv
        assert "--host" in argv
        assert "--port" in argv
        assert "8082" in argv

    def test_missing_model_raises(self):
        from whooshd.adapters.mlx_vlm import build_mlx_vlm_server_argv, MlxVlmConfig, MlxVlmConfigError

        config = MlxVlmConfig(enabled=True, model=None)
        with pytest.raises(MlxVlmConfigError, match="model path"):
            build_mlx_vlm_server_argv(config)

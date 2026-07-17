"""Tests for multi-runtime routing, model resolution, health aggregation,
and per-runtime state reporting.

Covers:
  * router resolves llama.cpp for GGUF models
  * router resolves MLX-LM Server for MLX models
  * disabled MLX runtime does not break GGUF
  * subprocess startup failure → runtime error, not global crash
  * warmup state is not reported as offline
  * /health/runtime exposes per-runtime state
  * /v1/models includes models from all runtimes
  * /api/tags remains compatible
  * ModelResolutionError for unknown models with multiple runtimes
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app, reset_router, _init_router
from whooshd.routing import (
    ModelResolutionError,
    RuntimeRouter,
    get_router,
    reset_router as routing_reset,
)
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatMessage,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeKind,
    RuntimeModel,
)
from whooshd.runtime import RuntimeState, get_runtime


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_chat_request(model: str = "test-model") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
    )


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_router():
    """Reset the router singleton before each test and restore it afterward."""
    routing_reset()
    router = get_router()
    yield router
    # Restore the router to its module-load state (stub-only).
    routing_reset()
    _init_router()


@pytest.fixture
def mock_llama_adapter():
    """Return a mock llama.cpp adapter."""
    adapter = MagicMock()
    adapter.kind = RuntimeKind.LLAMA_CPP.value
    adapter.name = "llama-cpp"
    adapter.supports_streaming = True
    adapter.model_id.return_value = "/models/test.gguf"
    adapter.is_loaded.return_value = False

    adapter.health = AsyncMock(return_value=RuntimeHealth(
        kind=RuntimeKind.LLAMA_CPP.value,
        enabled=True,
        state=RuntimeHealthState.OFFLINE,
        active_model=None,
        detail="No server configured.",
    ))
    adapter.list_models = AsyncMock(return_value=[
        RuntimeModel(
            id="/models/test.gguf",
            display_name="test.gguf",
            runtime=RuntimeKind.LLAMA_CPP.value,
            format="gguf",
            path="/models/test.gguf",
            context_window=32768,
            loaded=False,
            state=RuntimeHealthState.OFFLINE.value,
        )
    ])
    adapter.chat_completion = AsyncMock()
    adapter.chat_completion_stream = MagicMock()
    adapter.generate = AsyncMock()
    adapter.warmup = AsyncMock()
    adapter.unload = AsyncMock()
    return adapter


@pytest.fixture
def mock_mlx_server_adapter():
    """Return a mock MLX-LM Server adapter."""
    adapter = MagicMock()
    adapter.kind = RuntimeKind.MLX_LM_SERVER.value
    adapter.name = "mlx-lm-server"
    adapter.supports_streaming = True
    adapter.model_id.return_value = "mlx-community/test-model"
    adapter.is_loaded.return_value = False

    adapter.health = AsyncMock(return_value=RuntimeHealth(
        kind=RuntimeKind.MLX_LM_SERVER.value,
        enabled=True,
        state=RuntimeHealthState.MODEL_WARMING,
        active_model="mlx-community/test-model",
        detail="mlx_lm.server is warming up.",
    ))
    adapter.list_models = AsyncMock(return_value=[
        RuntimeModel(
            id="mlx-community/test-model",
            display_name="test-model",
            runtime=RuntimeKind.MLX_LM_SERVER.value,
            format="mlx",
            path="mlx-community/test-model",
            context_window=None,
            loaded=False,
            state=RuntimeHealthState.MODEL_WARMING.value,
        )
    ])
    adapter.chat_completion = AsyncMock()
    adapter.chat_completion_stream = MagicMock()
    adapter.generate = AsyncMock()
    adapter.warmup = AsyncMock()
    adapter.unload = AsyncMock()
    return adapter


# ── Router model resolution ─────────────────────────────────────────────────


class TestModelResolution:
    """Route model IDs to the correct runtime adapter."""

    def test_router_selects_llama_cpp_for_gguf(self, clean_router, mock_llama_adapter):
        """GGUF extension → llama_cpp runtime."""
        clean_router.register(mock_llama_adapter)

        async def _run():
            adapter = await clean_router._resolve_model_runtime("/models/test.gguf")
            assert adapter.kind == RuntimeKind.LLAMA_CPP.value

        import asyncio
        asyncio.run(_run())

    def test_router_uses_registry_engine_mapping(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        """When a registry entry maps model → engine, use that runtime."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            ModelRegistryConfig,
            RegistryModelEntry,
        )

        registry = ModelRegistryConfig(
            models={
                "my-gguf-model": RegistryModelEntry(
                    display_name="My GGUF",
                    engine=EngineType.LLAMA_CPP,
                    format=ModelFormat.GGUF,
                    path="/models/my.gguf",
                    modalities=[ModelModality.TEXT],
                ),
                "my-mlx-model": RegistryModelEntry(
                    display_name="My MLX",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/my-model",
                    modalities=[ModelModality.TEXT],
                ),
            }
        )

        # Inject registry into runtime, saving and restoring original.
        rt = get_runtime()
        original_registry = rt._registry
        rt._registry = registry

        try:
            clean_router.register(mock_llama_adapter)
            clean_router.register(mock_mlx_server_adapter)

            async def _run():
                adapter_gguf = await clean_router._resolve_model_runtime("my-gguf-model")
                assert adapter_gguf.kind == RuntimeKind.LLAMA_CPP.value

                adapter_mlx = await clean_router._resolve_model_runtime("my-mlx-model")
                assert adapter_mlx.kind == RuntimeKind.MLX_LM_SERVER.value

            import asyncio
            asyncio.run(_run())
        finally:
            rt._registry = original_registry

    def test_explicit_registry_rejects_unknown_model(
        self, clean_router, mock_mlx_server_adapter, monkeypatch
    ):
        """An explicit runtime registry is an authoritative allowlist."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            ModelRegistryConfig,
            RegistryModelEntry,
        )

        registry = ModelRegistryConfig(
            models={
                "allowed-model": RegistryModelEntry(
                    display_name="Allowed model",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/allowed-model",
                    modalities=[ModelModality.TEXT],
                )
            }
        )
        rt = get_runtime()
        original_registry = rt._registry
        rt._registry = registry
        monkeypatch.setenv("WHOOSHD_MODEL_REGISTRY_PATH", "/tmp/guest-registry.yaml")

        try:
            clean_router.register(mock_mlx_server_adapter)

            async def _run():
                with pytest.raises(
                    ModelResolutionError,
                    match="not allowed by the active runtime registry",
                ):
                    await clean_router._resolve_model_runtime("hidden-model")

            import asyncio
            asyncio.run(_run())
        finally:
            rt._registry = original_registry

    def test_explicit_registry_rejects_disabled_model(
        self, clean_router, mock_mlx_server_adapter, monkeypatch
    ):
        """Disabled registry entries cannot reach a compatible adapter."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            ModelRegistryConfig,
            RegistryModelEntry,
        )

        registry = ModelRegistryConfig(
            models={
                "disabled-model": RegistryModelEntry(
                    display_name="Disabled model",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/disabled-model",
                    modalities=[ModelModality.TEXT],
                    enabled=False,
                )
            }
        )
        rt = get_runtime()
        original_registry = rt._registry
        rt._registry = registry
        monkeypatch.setenv("WHOOSHD_MODEL_REGISTRY_PATH", "/tmp/guest-registry.yaml")

        try:
            clean_router.register(mock_mlx_server_adapter)

            async def _run():
                with pytest.raises(
                    ModelResolutionError,
                    match="disabled by the active runtime registry",
                ):
                    await clean_router._resolve_model_runtime("disabled-model")

            import asyncio
            asyncio.run(_run())
        finally:
            rt._registry = original_registry

    def test_router_fallback_to_only_non_stub_adapter(self, clean_router, mock_mlx_server_adapter):
        """When only one non-stub adapter is registered, unknown models route there."""
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            adapter = await clean_router._resolve_model_runtime("unknown-model")
            assert adapter.kind == RuntimeKind.MLX_LM_SERVER.value

        import asyncio
        asyncio.run(_run())

    def test_router_fallback_to_stub_when_alone(self, clean_router):
        """When only stub is registered, unknown models route to stub."""
        from whooshd.adapters.stub import StubInferenceAdapter
        clean_router.register(StubInferenceAdapter())

        async def _run():
            adapter = await clean_router._resolve_model_runtime("any-model")
            assert adapter.kind == RuntimeKind.STUB.value

        import asyncio
        asyncio.run(_run())

    def test_router_model_resolution_error_with_multiple_runtimes(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        """With multiple runtimes registered and no match, raise ModelResolutionError."""
        clean_router.register(mock_llama_adapter)
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            with pytest.raises(ModelResolutionError, match="does not match any registered runtime"):
                await clean_router._resolve_model_runtime("unknown-model-xyz")

        import asyncio
        asyncio.run(_run())


# ── Model inventory aggregation ─────────────────────────────────────────────


class TestModelInventoryAggregation:
    """Model lists aggregate across all registered runtimes."""

    def test_list_models_includes_all_runtimes(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        clean_router.register(mock_llama_adapter)
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            models = await clean_router.list_models()
            assert len(models) == 2
            kinds = {m.runtime for m in models}
            assert RuntimeKind.LLAMA_CPP.value in kinds
            assert RuntimeKind.MLX_LM_SERVER.value in kinds

        import asyncio
        asyncio.run(_run())

    def test_list_models_handles_adapter_errors_gracefully(self, clean_router, mock_llama_adapter):
        """If one adapter fails to list models, the others still succeed."""
        mock_llama_adapter.list_models = AsyncMock(side_effect=RuntimeError("boom"))
        clean_router.register(mock_llama_adapter)

        from whooshd.adapters.stub import StubInferenceAdapter
        clean_router.register(StubInferenceAdapter())

        async def _run():
            models = await clean_router.list_models()
            # Should still have stub models even if llama failed.
            assert len(models) >= 1

        import asyncio
        asyncio.run(_run())


# ── Health aggregation ──────────────────────────────────────────────────────


class TestHealthAggregation:
    """Per-runtime health is exposed without collapsing warmup into offline."""

    def test_health_reports_per_runtime_state(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        clean_router.register(mock_llama_adapter)
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            health = await clean_router.health()
            assert health.status in ("ok", "degraded")
            assert RuntimeKind.LLAMA_CPP.value in health.runtimes
            assert RuntimeKind.MLX_LM_SERVER.value in health.runtimes

            # llama.cpp: offline (not started)
            assert health.runtimes[RuntimeKind.LLAMA_CPP.value].state == RuntimeHealthState.OFFLINE

            # mlx_lm_server: model_warming (warming, not offline)
            mlx_state = health.runtimes[RuntimeKind.MLX_LM_SERVER.value].state
            assert mlx_state == RuntimeHealthState.MODEL_WARMING, (
                f"Expected model_warming, got {mlx_state}. "
                "Warmup must not be collapsed into offline."
            )

        import asyncio
        asyncio.run(_run())

    def test_health_degraded_when_runtime_errors(self, clean_router, mock_llama_adapter):
        """When a runtime reports error state, aggregate is degraded."""
        mock_llama_adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value,
            enabled=True,
            state=RuntimeHealthState.ERROR,
            active_model=None,
            detail="Process crashed.",
        ))
        clean_router.register(mock_llama_adapter)

        async def _run():
            health = await clean_router.health()
            assert health.status == "degraded"

        import asyncio
        asyncio.run(_run())

    def test_health_aggregate_ok_when_all_ready(self, clean_router, mock_llama_adapter):
        """When all runtimes are ready, aggregate is ok."""
        mock_llama_adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value,
            enabled=True,
            state=RuntimeHealthState.READY,
            active_model="/models/test.gguf",
            detail="Ready.",
        ))
        clean_router.register(mock_llama_adapter)

        async def _run():
            health = await clean_router.health()
            assert health.status == "ok"

        import asyncio
        asyncio.run(_run())


# ── Health/runtime HTTP endpoint ────────────────────────────────────────────


class TestHealthRuntimeEndpoint:
    """GET /health/runtime returns per-runtime health."""

    @pytest.mark.asyncio
    async def test_health_runtime_returns_200(self):
        """The endpoint exists and returns 200."""
        routing_reset()
        _init_router()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/runtime")
            assert resp.status_code == 200
            body = resp.json()
            assert "status" in body
            assert "runtimes" in body
            assert isinstance(body["runtimes"], dict)

    @pytest.mark.asyncio
    async def test_health_runtime_includes_stub(self):
        """Stub adapter always appears in health (when no others registered)."""
        routing_reset()
        _init_router()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/runtime")
            body = resp.json()
            assert "stub" in body["runtimes"]
            stub_health = body["runtimes"]["stub"]
            assert stub_health["enabled"] is True
            assert stub_health["state"] == "ready"


# ── Disabled runtime does not break other runtimes ──────────────────────────


class TestDisabledRuntime:
    """A disabled or failing runtime should not crash the router or
    other runtimes."""

    def test_disabled_mlx_does_not_break_llama_cpp(self, clean_router, mock_llama_adapter):
        """llama.cpp reports healthy even when mlx_lm_server is disabled."""
        mock_llama_adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value,
            enabled=True,
            state=RuntimeHealthState.READY,
            active_model="/models/test.gguf",
            detail="Ready.",
        ))
        clean_router.register(mock_llama_adapter)

        async def _run():
            health = await clean_router.health()
            assert health.status == "ok"
            llama_health = health.runtimes[RuntimeKind.LLAMA_CPP.value]
            assert llama_health.state == RuntimeHealthState.READY

        import asyncio
        asyncio.run(_run())

    def test_adapter_health_exception_does_not_crash_router(self, clean_router, mock_llama_adapter):
        """If one adapter's health() raises, the router survives."""
        mock_llama_adapter.health = AsyncMock(side_effect=RuntimeError("health probe crash"))

        from whooshd.adapters.stub import StubInferenceAdapter
        clean_router.register(StubInferenceAdapter())
        clean_router.register(mock_llama_adapter)

        async def _run():
            health = await clean_router.health()
            # Should still have stub health, even if llama crashed.
            assert "stub" in health.runtimes
            assert health.status == "degraded"  # Because llama errored

        import asyncio
        asyncio.run(_run())


# ── Warmup state distinction ────────────────────────────────────────────────


class TestWarmupStateDistinction:
    """Warmup state must not be collapsed into offline."""

    def test_warmup_is_not_offline(self, clean_router, mock_mlx_server_adapter):
        """model_warming state is distinct from offline."""
        mock_mlx_server_adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.MLX_LM_SERVER.value,
            enabled=True,
            state=RuntimeHealthState.MODEL_WARMING,
            active_model="mlx-community/test-model",
            detail="Model is loading...",
        ))
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            health = await clean_router.health()
            mlx_state = health.runtimes[RuntimeKind.MLX_LM_SERVER.value].state
            assert mlx_state == RuntimeHealthState.MODEL_WARMING
            assert mlx_state != RuntimeHealthState.OFFLINE

        import asyncio
        asyncio.run(_run())


# ── Subprocess startup failure → runtime error, not global crash ────────────


class TestSubprocessFailure:
    """When a runtime backend fails to start, the error is scoped to that
    runtime — Whoosh'd itself stays alive."""

    def test_startup_failure_is_runtime_error_not_crash(self, clean_router, mock_mlx_server_adapter):
        """A runtime that fails to start reports 'error' state, not a crash."""
        mock_mlx_server_adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.MLX_LM_SERVER.value,
            enabled=True,
            state=RuntimeHealthState.ERROR,
            active_model=None,
            detail="mlx_lm.server exited unexpectedly with code 1.",
        ))
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            health = await clean_router.health()
            assert health.status == "degraded"
            mlx_state = health.runtimes[RuntimeKind.MLX_LM_SERVER.value].state
            assert mlx_state == RuntimeHealthState.ERROR

        import asyncio
        asyncio.run(_run())


# ── /v1/models includes models from all runtimes ────────────────────────────


class TestOpenAIModelListMultiRuntime:
    """GET /v1/models with registry shows models from all runtimes."""

    @pytest.mark.asyncio
    async def test_v1_models_includes_all_registry_models(self):
        """When a registry with both GGUF and MLX entries is loaded,
        /v1/models returns both."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            ModelRegistryConfig,
            RegistryModelEntry,
        )

        registry = ModelRegistryConfig(
            models={
                "test-gguf": RegistryModelEntry(
                    display_name="Test GGUF",
                    engine=EngineType.LLAMA_CPP,
                    format=ModelFormat.GGUF,
                    path="/models/test.gguf",
                    modalities=[ModelModality.TEXT],
                ),
                "test-mlx": RegistryModelEntry(
                    display_name="Test MLX",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/test",
                    modalities=[ModelModality.TEXT],
                ),
            }
        )

        rt = get_runtime()
        original_registry = rt._registry
        rt._registry = registry
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                assert resp.status_code == 200
                body = resp.json()
                assert len(body["data"]) == 2
                model_ids = {m["id"] for m in body["data"]}
                assert "test-gguf" in model_ids
                assert "test-mlx" in model_ids

                # Verify metadata includes engine/format.
                gguf_entry = next(m for m in body["data"] if m["id"] == "test-gguf")
                assert gguf_entry["metadata"]["engine"] == "llama_cpp"
                assert gguf_entry["metadata"]["format"] == "gguf"

                mlx_entry = next(m for m in body["data"] if m["id"] == "test-mlx")
                assert mlx_entry["metadata"]["engine"] == "mlx_lm"
                assert mlx_entry["metadata"]["format"] == "mlx"
        finally:
            rt._registry = original_registry


# ── /api/tags remains compatible ────────────────────────────────────────────


class TestOllamaTagsMultiRuntime:
    """GET /api/tags remains Ollama-compatible with multi-runtime."""

    @pytest.mark.asyncio
    async def test_api_tags_includes_all_registry_models(self):
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            ModelRegistryConfig,
            RegistryModelEntry,
        )

        registry = ModelRegistryConfig(
            models={
                "test-gguf": RegistryModelEntry(
                    display_name="Test GGUF",
                    engine=EngineType.LLAMA_CPP,
                    format=ModelFormat.GGUF,
                    path="/models/test.gguf",
                    modalities=[ModelModality.TEXT],
                ),
                "test-mlx": RegistryModelEntry(
                    display_name="Test MLX",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/test",
                    modalities=[ModelModality.TEXT],
                ),
            }
        )

        rt = get_runtime()
        original_registry = rt._registry
        rt._registry = registry
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/tags")
                assert resp.status_code == 200
                body = resp.json()
                assert len(body["models"]) == 2
                names = {m["name"] for m in body["models"]}
                assert "test-gguf" in names
                assert "test-mlx" in names

                # Verify Ollama-compatible detail fields.
                gguf_tag = next(m for m in body["models"] if m["name"] == "test-gguf")
                assert gguf_tag["details"]["format"] == "gguf"
                assert gguf_tag["details"]["family"] == "llama_cpp"

                mlx_tag = next(m for m in body["models"] if m["name"] == "test-mlx")
                assert mlx_tag["details"]["format"] == "mlx"
                assert mlx_tag["details"]["family"] == "mlx_lm"
        finally:
            rt._registry = original_registry


# ── MLX-LM Server adapter unit tests ────────────────────────────────────────


class TestMlxLmServerAdapter:
    """Unit tests for the MLX-LM Server adapter."""

    def test_adapter_identity(self):
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=8081,
            model="mlx-community/test-model",
        )
        adapter = MlxLmServerAdapter(config=config)
        assert adapter.name == "mlx-lm-server"
        assert adapter.kind == RuntimeKind.MLX_LM_SERVER.value
        assert adapter.supports_streaming is True
        assert adapter.model_id() == "mlx-community/test-model"
        assert adapter.is_loaded() is False

    def test_adapter_disabled_reports_offline(self):
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(enabled=False)
        adapter = MlxLmServerAdapter(config=config)

        async def _run():
            h = await adapter.health()
            assert h.enabled is False
            assert h.state == RuntimeHealthState.OFFLINE
            assert "disabled" in (h.detail or "").lower()

        import asyncio
        asyncio.run(_run())

    def test_adapter_no_model_reports_offline(self):
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(enabled=True, model=None)
        adapter = MlxLmServerAdapter(config=config)

        async def _run():
            h = await adapter.health()
            assert h.enabled is True
            assert h.state == RuntimeHealthState.OFFLINE

        import asyncio
        asyncio.run(_run())

    def test_list_models_returns_empty_when_no_model(self):
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(enabled=True, model=None)
        adapter = MlxLmServerAdapter(config=config)

        async def _run():
            models = await adapter.list_models()
            assert models == []

        import asyncio
        asyncio.run(_run())

    def test_list_models_returns_model_when_configured(self):
        from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter, MlxLmServerConfig

        config = MlxLmServerConfig(
            enabled=True,
            model="mlx-community/test-model",
        )
        adapter = MlxLmServerAdapter(config=config)
        # Override the managed process check — no real process.
        adapter._managed_process = MagicMock()
        adapter._managed_process.is_running = True
        adapter._managed_process.check_exited.return_value = False

        async def _run():
            models = await adapter.list_models()
            assert len(models) == 1
            assert models[0].id == "mlx-community/test-model"
            assert models[0].runtime == RuntimeKind.MLX_LM_SERVER.value
            assert models[0].format == "mlx"

        import asyncio
        asyncio.run(_run())

    def test_build_argv(self):
        from whooshd.adapters.mlx_lm_server import build_mlx_lm_server_argv, MlxLmServerConfig

        config = MlxLmServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=8081,
            model="mlx-community/test-model",
        )
        argv = build_mlx_lm_server_argv(config)
        assert argv[0] == "python"
        assert argv[1] == "-m"
        assert argv[2] == "mlx_lm"
        assert argv[3] == "server"
        assert "--model" in argv
        assert "mlx-community/test-model" in argv
        assert "--host" in argv
        assert "127.0.0.1" in argv
        assert "--port" in argv
        assert "8081" in argv

    def test_build_argv_extra_args(self):
        from whooshd.adapters.mlx_lm_server import build_mlx_lm_server_argv, MlxLmServerConfig

        config = MlxLmServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=8081,
            model="mlx-community/test-model",
            extra_args=["--max-tokens", "4096"],
        )
        argv = build_mlx_lm_server_argv(config)
        assert "--max-tokens" in argv
        assert "4096" in argv

    def test_inference_raises_when_server_unavailable(self):
        """Without a running server, inference raises RuntimeUnavailable."""
        from whooshd.adapters.mlx_lm_server import (
            MlxLmServerAdapter,
            MlxLmServerConfig,
        )
        from whooshd.http_forwarding import RuntimeUnavailable

        config = MlxLmServerConfig(enabled=True, model="test")
        adapter = MlxLmServerAdapter(config=config)

        # Mock the server probe to simulate connection refused.
        import httpx as real_httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        with patch("whooshd.adapters.mlx_lm_server.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_client.post = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_client.stream = MagicMock(side_effect=real_httpx.ConnectError("refused"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            import asyncio

            # generate — should raise RuntimeUnavailable (server not reachable).
            with pytest.raises(RuntimeUnavailable):
                from whooshd.contracts import GenerateRequest
                asyncio.run(adapter.generate(GenerateRequest(prompt="Hello")))

            # chat_completion — should raise RuntimeUnavailable.
            with pytest.raises(RuntimeUnavailable):
                asyncio.run(adapter.chat_completion(_make_chat_request()))

    def test_config_from_env_disabled_by_default(self, monkeypatch):
        """MLX-LM Server is disabled by default."""
        monkeypatch.delenv("WHOOSHD_MLX_ENABLED", raising=False)
        from whooshd.adapters.mlx_lm_server import _build_config_from_env
        config = _build_config_from_env()
        assert config.enabled is False

    def test_config_from_env_enabled(self, monkeypatch):
        """Setting WHOOSHD_MLX_ENABLED=true enables the runtime."""
        monkeypatch.setenv("WHOOSHD_MLX_ENABLED", "true")
        monkeypatch.setenv("WHOOSHD_MLX_MODEL", "mlx-community/test")
        monkeypatch.setenv("WHOOSHD_MLX_HOST", "0.0.0.0")
        monkeypatch.setenv("WHOOSHD_MLX_PORT", "9090")
        from whooshd.adapters.mlx_lm_server import _build_config_from_env
        config = _build_config_from_env()
        assert config.enabled is True
        assert config.model == "mlx-community/test"
        assert config.host == "0.0.0.0"
        assert config.port == 9090


# ── Router warmup_all / unload_all ──────────────────────────────────────────


class TestRouterLifecycle:
    def test_warmup_all_calls_all_adapters(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        clean_router.register(mock_llama_adapter)
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            results = await clean_router.warmup_all()
            assert RuntimeKind.LLAMA_CPP.value in results
            assert RuntimeKind.MLX_LM_SERVER.value in results
            mock_llama_adapter.warmup.assert_called_once()
            mock_mlx_server_adapter.warmup.assert_called_once()

        import asyncio
        asyncio.run(_run())

    def test_unload_all_calls_all_adapters(self, clean_router, mock_llama_adapter, mock_mlx_server_adapter):
        clean_router.register(mock_llama_adapter)
        clean_router.register(mock_mlx_server_adapter)

        async def _run():
            results = await clean_router.unload_all()
            assert RuntimeKind.LLAMA_CPP.value in results
            assert RuntimeKind.MLX_LM_SERVER.value in results
            mock_llama_adapter.unload.assert_called_once()
            mock_mlx_server_adapter.unload.assert_called_once()

        import asyncio
        asyncio.run(_run())


# ── RuntimeKind enum ────────────────────────────────────────────────────────


class TestRuntimeKindEnum:
    def test_all_kinds_present(self):
        assert RuntimeKind.STUB.value == "stub"
        assert RuntimeKind.MLX_LM.value == "mlx_lm"
        assert RuntimeKind.MLX_LM_SERVER.value == "mlx_lm_server"
        assert RuntimeKind.LLAMA_CPP.value == "llama_cpp"

    def test_runtime_health_state_values(self):
        """All required states are present."""
        states = {s.value for s in RuntimeHealthState}
        required = {"offline", "starting", "runtime_available", "model_warming",
                     "ready", "generating", "degraded", "error"}
        assert required.issubset(states)


# ── Stable session identity tests ──────────────────────────────────────────


class TestStableSessionIdentity:
    @pytest.mark.asyncio
    async def test_health_runtime_includes_session(self):
        """GET /health/runtime includes session metadata."""
        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/runtime")
            assert resp.status_code == 200
            body = resp.json()
            assert "session" in body
            session = body["session"]
            assert "pid" in session
            assert "session_id" in session
            assert "started_at" in session
            assert "registered_runtime_kinds" in session
            assert isinstance(session["pid"], int)
            assert isinstance(session["session_id"], str)
            assert len(session["session_id"]) > 0

    @pytest.mark.asyncio
    async def test_session_id_stable_across_calls(self):
        """Session ID is stable across repeated /health/runtime calls."""
        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.get("/health/runtime")
            resp2 = await client.get("/health/runtime")
            sid1 = resp1.json()["session"]["session_id"]
            sid2 = resp2.json()["session"]["session_id"]
            assert sid1 == sid2, f"Session ID changed: {sid1} -> {sid2}"
            assert len(sid1) > 0

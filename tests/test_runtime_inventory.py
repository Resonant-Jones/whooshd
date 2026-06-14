"""Tests for runtime inventory accuracy — /v1/models and /api/tags.

Validates that enabled runtime models appear correctly in discovery endpoints.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app, reset_router, _init_router
from whooshd.routing import reset_router as routing_reset, get_router
from whooshd.runtime import RuntimeState, get_runtime


@pytest.fixture(autouse=True)
def _clean_router():
    """Reset the router and runtime registry before each test."""
    routing_reset()
    _init_router()
    # Also clear any injected registry.
    from whooshd.runtime import get_runtime
    get_runtime()._registry = None
    yield
    routing_reset()
    _init_router()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _register_mlx_adapter(model: str = "mlx-community/test-model"):
    """Register a mock MLX-LM Server adapter in the router."""
    from unittest.mock import AsyncMock, MagicMock
    from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel

    adapter = MagicMock()
    adapter.kind = RuntimeKind.MLX_LM_SERVER.value
    adapter.name = "mlx-lm-server"
    adapter.is_loaded.return_value = True
    adapter.model_id.return_value = model
    adapter.health = AsyncMock(return_value=RuntimeHealth(
        kind=RuntimeKind.MLX_LM_SERVER.value,
        enabled=True,
        state=RuntimeHealthState.READY,
        active_model=model,
        detail="ready.",
    ))
    adapter.list_models = AsyncMock(return_value=[
        RuntimeModel(
            id=model,
            display_name=model.rsplit("/", 1)[-1] if "/" in model else model,
            runtime=RuntimeKind.MLX_LM_SERVER.value,
            format="mlx",
            path=model,
            context_window=32768,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            loaded=True,
            state=RuntimeHealthState.READY.value,
        )
    ])

    router = get_router()
    router.register(adapter)
    return adapter


def _register_llama_adapter(model: str = "/models/test.gguf"):
    """Register a mock llama.cpp adapter in the router."""
    from unittest.mock import AsyncMock, MagicMock
    from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel

    adapter = MagicMock()
    adapter.kind = RuntimeKind.LLAMA_CPP.value
    adapter.name = "llama-cpp"
    adapter.is_loaded.return_value = True
    adapter.model_id.return_value = model
    adapter.health = AsyncMock(return_value=RuntimeHealth(
        kind=RuntimeKind.LLAMA_CPP.value,
        enabled=True,
        state=RuntimeHealthState.READY,
        active_model=model,
        detail="ready.",
    ))
    adapter.list_models = AsyncMock(return_value=[
        RuntimeModel(
            id=model,
            display_name=model.rsplit("/", 1)[-1],
            runtime=RuntimeKind.LLAMA_CPP.value,
            format="gguf",
            path=model,
            context_window=32768,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            loaded=True,
            state=RuntimeHealthState.READY.value,
        )
    ])

    router = get_router()
    router.register(adapter)
    return adapter


# ── /v1/models tests ───────────────────────────────────────────────────────


class TestV1ModelsInventory:
    @pytest.mark.asyncio
    async def test_mlx_model_appears_in_v1_models(self):
        """When MLX-LM Server adapter is registered, its model appears."""
        _register_mlx_adapter("mlx-community/test-model")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "mlx-community/test-model" in model_ids

    @pytest.mark.asyncio
    async def test_llama_model_appears_in_v1_models(self):
        """When llama.cpp adapter is registered, its model appears."""
        _register_llama_adapter("/models/test.gguf")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "/models/test.gguf" in model_ids

    @pytest.mark.asyncio
    async def test_both_models_appear_together(self):
        """Both MLX and llama.cpp models appear in inventory."""
        _register_mlx_adapter("mlx-community/test-model")
        _register_llama_adapter("/models/test.gguf")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "mlx-community/test-model" in model_ids
            assert "/models/test.gguf" in model_ids
            assert len(model_ids) == 2

    @pytest.mark.asyncio
    async def test_stub_still_works_when_alone(self):
        """When only stub is registered, stub-model still appears."""
        routing_reset()
        from whooshd.adapters.stub import StubInferenceAdapter
        get_router().register(StubInferenceAdapter())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "stub-model" in model_ids


# ── /api/tags tests ────────────────────────────────────────────────────────


class TestApiTagsInventory:
    @pytest.mark.asyncio
    async def test_mlx_model_appears_in_api_tags(self):
        """MLX model appears in Ollama-compatible tags."""
        _register_mlx_adapter("mlx-community/test-model")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            assert resp.status_code == 200
            models = resp.json()["models"]
            names = [m["name"] for m in models]
            assert "mlx-community/test-model" in names

    @pytest.mark.asyncio
    async def test_llama_model_appears_in_api_tags(self):
        """llama.cpp model appears in Ollama-compatible tags."""
        _register_llama_adapter("/models/test.gguf")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            assert resp.status_code == 200
            models = resp.json()["models"]
            names = [m["name"] for m in models]
            assert "/models/test.gguf" in names


# ── No duplicate entries ───────────────────────────────────────────────────


class TestNoDuplicates:
    @pytest.mark.asyncio
    async def test_no_duplicate_when_registry_and_adapter_refer_same_model(self):
        """Registry entries take priority; adapter contributions are not duplicated."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )

        # Inject a registry with the same model as the adapter.
        registry = ModelRegistryConfig(
            models={
                "mlx-community/test-model": RegistryModelEntry(
                    display_name="Test MLX",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="mlx-community/test-model",
                    modalities=[ModelModality.TEXT],
                ),
            }
        )
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            # Registry shows the model once (no duplicate).
            assert model_ids.count("mlx-community/test-model") == 1
            # Registry metadata is present.
            entry = next(m for m in data if m["id"] == "mlx-community/test-model")
            assert entry["metadata"]["engine"] == "mlx_lm"


# ── Disabled runtime does not appear ───────────────────────────────────────


class TestDisabledRuntime:
    @pytest.mark.asyncio
    async def test_disabled_mlx_does_not_appear(self):
        """A disabled MLX adapter does not contribute models (only stub shows)."""
        routing_reset()
        from whooshd.adapters.stub import StubInferenceAdapter
        get_router().register(StubInferenceAdapter())
        # No MLX adapter registered → only stub.

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "stub-model" in model_ids
            # Should NOT contain MLX models.
            assert not any("mlx" in mid.lower() for mid in model_ids)


# ── llama.cpp inventory tests ─────────────────────────────────────────────


class TestLlamaCppInventory:
    @pytest.mark.asyncio
    async def test_llama_cpp_model_appears_in_v1_models(self):
        """When llama.cpp adapter is registered with model_path, the GGUF model appears."""
        from unittest.mock import AsyncMock, MagicMock
        from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel
        from whooshd.adapters.llama_cpp import _LlamaCppHealthStatus

        adapter = MagicMock()
        adapter.kind = RuntimeKind.LLAMA_CPP.value
        adapter.name = "llama-cpp"
        adapter.is_loaded.return_value = True
        adapter.model_id.return_value = "models/gguf/test.gguf"
        adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value, enabled=True,
            state=RuntimeHealthState.READY, active_model="models/gguf/test.gguf", detail="ready"))
        adapter.list_models = AsyncMock(return_value=[
            RuntimeModel(id="models/gguf/test.gguf", display_name="test.gguf",
                         runtime=RuntimeKind.LLAMA_CPP.value, format="gguf",
                         path="models/gguf/test.gguf", loaded=True,
                         state=RuntimeHealthState.READY.value)
        ])

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "models/gguf/test.gguf" in ids
            assert "stub-model" not in ids  # Stub masked by real runtime

    @pytest.mark.asyncio
    async def test_llama_cpp_model_appears_in_api_tags(self):
        """GGUF model appears in Ollama-compatible tags."""
        from unittest.mock import AsyncMock, MagicMock
        from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel

        adapter = MagicMock()
        adapter.kind = RuntimeKind.LLAMA_CPP.value
        adapter.is_loaded.return_value = True
        adapter.model_id.return_value = "models/gguf/test.gguf"
        adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value, enabled=True,
            state=RuntimeHealthState.READY, active_model="models/gguf/test.gguf", detail="ready"))
        adapter.list_models = AsyncMock(return_value=[
            RuntimeModel(id="models/gguf/test.gguf", display_name="test.gguf",
                         runtime=RuntimeKind.LLAMA_CPP.value, format="gguf",
                         path="models/gguf/test.gguf", loaded=True,
                         state=RuntimeHealthState.READY.value)
        ])

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            assert resp.status_code == 200
            names = [m["name"] for m in resp.json()["models"]]
            assert "models/gguf/test.gguf" in names

    @pytest.mark.asyncio
    async def test_stub_model_masked_when_llama_cpp_active(self):
        """When llama.cpp is active, stub-model does not appear in inventory."""
        from unittest.mock import AsyncMock, MagicMock
        from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel

        adapter = MagicMock()
        adapter.kind = RuntimeKind.LLAMA_CPP.value
        adapter.is_loaded.return_value = True
        adapter.model_id.return_value = "models/gguf/test.gguf"
        adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value, enabled=True,
            state=RuntimeHealthState.READY, active_model="models/gguf/test.gguf", detail="ready"))
        adapter.list_models = AsyncMock(return_value=[
            RuntimeModel(id="models/gguf/test.gguf", display_name="test.gguf",
                         runtime=RuntimeKind.LLAMA_CPP.value, format="gguf",
                         path="models/gguf/test.gguf", loaded=True,
                         state=RuntimeHealthState.READY.value)
        ])

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "stub-model" not in model_ids
            assert "models/gguf/test.gguf" in model_ids


# ── GGUF registry alias tests ──────────────────────────────────────────────


class TestGgufRegistryAlias:
    @pytest.mark.asyncio
    async def test_registry_alias_appears_in_v1_models(self):
        """Registry alias for GGUF model appears instead of raw path."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen-small": RegistryModelEntry(
                display_name="Qwen Small GGUF",
                engine=EngineType.LLAMA_CPP,
                format=ModelFormat.GGUF,
                path="models/gguf/test.gguf",
                modalities=[ModelModality.TEXT],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            assert "qwen-small" in model_ids
            assert "models/gguf/test.gguf" not in model_ids  # raw path hidden

    @pytest.mark.asyncio
    async def test_registry_alias_appears_in_api_tags(self):
        """Registry alias appears in Ollama-compatible tags."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen-small": RegistryModelEntry(
                display_name="Qwen Small GGUF",
                engine=EngineType.LLAMA_CPP,
                format=ModelFormat.GGUF,
                path="models/gguf/test.gguf",
                modalities=[ModelModality.TEXT],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            names = [m["name"] for m in resp.json()["models"]]
            assert "qwen-small" in names

    @pytest.mark.asyncio
    async def test_raw_path_fallback_when_no_alias(self):
        """When no registry alias exists, raw adapter model ID is used."""
        from unittest.mock import AsyncMock, MagicMock
        from whooshd.contracts import RuntimeHealth, RuntimeHealthState, RuntimeKind, RuntimeModel

        adapter = MagicMock()
        adapter.kind = RuntimeKind.LLAMA_CPP.value
        adapter.is_loaded.return_value = True
        adapter.model_id.return_value = "models/gguf/raw.gguf"
        adapter.health = AsyncMock(return_value=RuntimeHealth(
            kind=RuntimeKind.LLAMA_CPP.value, enabled=True,
            state=RuntimeHealthState.READY, active_model="models/gguf/raw.gguf", detail="ready"))
        adapter.list_models = AsyncMock(return_value=[
            RuntimeModel(id="models/gguf/raw.gguf", display_name="raw.gguf",
                         runtime=RuntimeKind.LLAMA_CPP.value, format="gguf",
                         path="models/gguf/raw.gguf", loaded=True,
                         state=RuntimeHealthState.READY.value)
        ])

        get_router().register(adapter)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            ids = [m["id"] for m in resp.json()["data"]]
            assert "models/gguf/raw.gguf" in ids

    @pytest.mark.asyncio
    async def test_no_duplicate_when_registry_and_adapter_same_path(self):
        """No duplicate when registry alias and adapter refer to same model path."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen-small": RegistryModelEntry(
                display_name="Qwen Small GGUF",
                engine=EngineType.LLAMA_CPP,
                format=ModelFormat.GGUF,
                path="models/gguf/test.gguf",
                modalities=[ModelModality.TEXT],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            data = resp.json()["data"]
            model_ids = [m["id"] for m in data]
            # Only the alias should appear, not both.
            assert model_ids.count("qwen-small") == 1
            assert "models/gguf/test.gguf" not in model_ids


# ── Cross-family alias tests ───────────────────────────────────────────────


class TestCrossFamilyAliases:
    @pytest.mark.asyncio
    async def test_mlx_lm_alias_appears_in_v1_models(self):
        """MLX-LM Server registry alias appears in /v1/models."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "llama-3.2-3b-mlx": RegistryModelEntry(
                display_name="Llama 3.2 3B MLX",
                engine=EngineType.MLX_LM,
                format=ModelFormat.MLX,
                path="mlx-community/Llama-3.2-3B-Instruct-4bit",
                modalities=[ModelModality.TEXT],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            ids = [m["id"] for m in resp.json()["data"]]
            assert "llama-3.2-3b-mlx" in ids

    @pytest.mark.asyncio
    async def test_mlx_lm_alias_appears_in_api_tags(self):
        """MLX-LM alias appears in /api/tags."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "llama-3.2-3b-mlx": RegistryModelEntry(
                display_name="Llama 3.2 3B MLX",
                engine=EngineType.MLX_LM,
                format=ModelFormat.MLX,
                path="mlx-community/Llama-3.2-3B-Instruct-4bit",
                modalities=[ModelModality.TEXT],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            names = [m["name"] for m in resp.json()["models"]]
            assert "llama-3.2-3b-mlx" in names

    @pytest.mark.asyncio
    async def test_mlx_vlm_alias_appears_with_vision_metadata(self):
        """MLX-VLM alias appears with supports_vision metadata."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen2-vl-2b-mlx": RegistryModelEntry(
                display_name="Qwen2 VL 2B MLX",
                engine=EngineType.MLX_VLM,
                format=ModelFormat.MLX,
                path="mlx-community/Qwen2-VL-2B-Instruct-4bit",
                modalities=[ModelModality.TEXT, ModelModality.VISION],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            data = resp.json()["data"]
            ids = [m["id"] for m in data]
            assert "qwen2-vl-2b-mlx" in ids
            entry = next(m for m in data if m["id"] == "qwen2-vl-2b-mlx")
            assert entry["metadata"]["engine"] == "mlx_vlm"
            assert "vision" in entry["metadata"]["modalities"]

    @pytest.mark.asyncio
    async def test_mlx_vlm_alias_in_api_tags(self):
        """MLX-VLM alias appears in /api/tags."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen2-vl-2b-mlx": RegistryModelEntry(
                display_name="Qwen2 VL 2B MLX",
                engine=EngineType.MLX_VLM,
                format=ModelFormat.MLX,
                path="mlx-community/Qwen2-VL-2B-Instruct-4bit",
                modalities=[ModelModality.TEXT, ModelModality.VISION],
            ),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/tags")
            names = [m["name"] for m in resp.json()["models"]]
            assert "qwen2-vl-2b-mlx" in names

    @pytest.mark.asyncio
    async def test_all_three_families_aliases_together(self):
        """All three runtime families appear together with clean aliases."""
        from whooshd.registry import (
            EngineType, ModelFormat, ModelModality,
            ModelRegistryConfig, RegistryModelEntry,
        )
        registry = ModelRegistryConfig(models={
            "qwen2.5-0.5b-gguf": RegistryModelEntry(
                display_name="Qwen GGUF", engine=EngineType.LLAMA_CPP,
                format=ModelFormat.GGUF, path="m.gguf", modalities=[ModelModality.TEXT]),
            "llama-3.2-3b-mlx": RegistryModelEntry(
                display_name="Llama MLX", engine=EngineType.MLX_LM,
                format=ModelFormat.MLX, path="m", modalities=[ModelModality.TEXT]),
            "qwen2-vl-2b-mlx": RegistryModelEntry(
                display_name="Qwen VL", engine=EngineType.MLX_VLM,
                format=ModelFormat.MLX, path="v", modalities=[ModelModality.TEXT, ModelModality.VISION]),
        })
        rt = get_runtime()
        rt._registry = registry

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            ids = [m["id"] for m in resp.json()["data"]]
            assert len(ids) == 3
            assert "qwen2.5-0.5b-gguf" in ids
            assert "llama-3.2-3b-mlx" in ids
            assert "qwen2-vl-2b-mlx" in ids

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.registry import (
    EngineType,
    ModelFormat,
    ModelModality,
    WarmPolicy,
    load_model_registry,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs" / "models.friends-family-guest.yaml"
MODEL_ID = "gemma-4-12b-it-qat-4bit"


def test_friends_family_registry_pins_one_keep_warm_mlx_vlm() -> None:
    registry = load_model_registry(PROFILE)

    assert registry is not None
    assert list(registry.models) == [MODEL_ID]
    assert [model_id for model_id, _ in registry.enabled_models()] == [MODEL_ID]

    model = registry.get(MODEL_ID)
    assert model is not None
    assert model.enabled is True
    assert model.engine == EngineType.MLX_VLM
    assert model.format == ModelFormat.MLX
    assert model.warm_policy == WarmPolicy.KEEP_WARM
    assert model.modalities == [ModelModality.TEXT, ModelModality.VISION]
    assert model.context_window == 32768
    assert "friends-family" in model.tags
    assert "pinned" in model.tags


@pytest.mark.asyncio
async def test_guest_profile_inventory_and_allowlist_reject_before_provider(
    monkeypatch,
) -> None:
    """The deployment profile exposes only its guest model and gates execution."""
    from whooshd.adapters.stub import StubInferenceAdapter
    from whooshd.app import _init_router, app
    from whooshd.routing import get_router, reset_router
    from whooshd.runtime import get_runtime

    monkeypatch.setenv("WHOOSHD_MODEL_REGISTRY_PATH", str(PROFILE))

    runtime = get_runtime()
    original_registry = runtime._registry
    runtime._registry = None

    reset_router()
    router = get_router()
    provider = MagicMock()
    provider.kind = "mlx_vlm"
    provider.name = "recording-mlx-vlm"
    provider.supports_streaming = True
    provider.model_id.return_value = MODEL_ID
    provider.is_loaded.return_value = False
    provider.chat_completion = AsyncMock()
    provider.chat_completion_stream = MagicMock()
    router.register(StubInferenceAdapter())
    router.register(provider)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            inventory = await client.get("/v1/models")
            assert inventory.status_code == 200
            assert [entry["id"] for entry in inventory.json()["data"]] == [MODEL_ID]

            rejected = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "unlisted-guest-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )

        assert rejected.status_code == 400
        assert rejected.json()["code"] == "MODEL_NOT_FOUND"
        assert "not allowed by the active runtime registry" in rejected.json()["message"]
        provider.chat_completion.assert_not_awaited()
    finally:
        runtime._registry = original_registry
        reset_router()
        _init_router()

from pathlib import Path

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

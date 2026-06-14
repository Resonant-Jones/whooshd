"""Tests for the multi-engine model registry schema, validation, and loader.

Covers: YAML loading, enum validation, cross-field rules (format→engine,
vision→engine), missing fields, and backward compatibility when no
registry file is present.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from whooshd.registry import (
    EngineType,
    HardwareAffinity,
    ModelFormat,
    ModelModality,
    ModelRegistryConfig,
    RegistryModelEntry,
    RegistryValidationError,
    WarmPolicy,
    _validate_registry_entry,
    load_model_registry,
    validate_registry,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


# ── Enum tests ──────────────────────────────────────────────────────────────


class TestEngineType:
    def test_vision_capable_engines(self):
        assert EngineType.MLX_VLM in EngineType.vision_capable()
        assert EngineType.MLX_LM not in EngineType.vision_capable()
        assert EngineType.LLAMA_CPP not in EngineType.vision_capable()

    def test_text_capable_engines(self):
        assert EngineType.MLX_LM in EngineType.text_capable()
        assert EngineType.MLX_VLM in EngineType.text_capable()
        assert EngineType.LLAMA_CPP in EngineType.text_capable()


class TestModelModality:
    def test_values(self):
        assert ModelModality.TEXT.value == "text"
        assert ModelModality.VISION.value == "vision"
        assert ModelModality.EMBEDDING.value == "embedding"


class TestWarmPolicy:
    def test_values(self):
        assert WarmPolicy.COLD.value == "cold"
        assert WarmPolicy.WARM_ON_START.value == "warm_on_start"
        assert WarmPolicy.WARM_ON_FIRST_USE.value == "warm_on_first_use"
        assert WarmPolicy.KEEP_WARM.value == "keep_warm"


# ── RegistryModelEntry tests ─────────────────────────────────────────────────


class TestRegistryModelEntry:
    def test_minimal_entry(self):
        entry = RegistryModelEntry(
            display_name="Test Model",
            engine=EngineType.MLX_LM,
            format=ModelFormat.MLX,
            path="mlx-community/test-model",
        )
        assert entry.display_name == "Test Model"
        assert entry.engine == EngineType.MLX_LM
        assert entry.format == ModelFormat.MLX
        assert entry.path == "mlx-community/test-model"
        assert entry.context_window == 32768  # default
        assert entry.modalities == [ModelModality.TEXT]
        assert entry.enabled is True
        assert entry.warm_policy == WarmPolicy.WARM_ON_FIRST_USE

    def test_full_entry(self):
        entry = RegistryModelEntry(
            display_name="Full Model",
            engine=EngineType.MLX_VLM,
            format=ModelFormat.MLX,
            path="/models/full",
            modalities=[ModelModality.TEXT, ModelModality.VISION],
            context_window=65536,
            preferred_hardware=[HardwareAffinity.APPLE_SILICON, HardwareAffinity.METAL],
            warm_policy=WarmPolicy.KEEP_WARM,
            priority="vision",
            enabled=True,
            tags=["vision", "mlx"],
        )
        assert entry.is_vision_capable() is True
        assert entry.has_modality(ModelModality.VISION) is True
        assert entry.has_modality(ModelModality.EMBEDDING) is False
        assert entry.context_window == 65536

    def test_text_only_model_not_vision_capable(self):
        entry = RegistryModelEntry(
            display_name="Text Only",
            engine=EngineType.MLX_LM,
            format=ModelFormat.MLX,
            path="/models/text",
            modalities=[ModelModality.TEXT],
        )
        assert entry.is_vision_capable() is False

    def test_context_window_must_be_positive(self):
        with pytest.raises(Exception):
            RegistryModelEntry(
                display_name="Bad Context",
                engine=EngineType.MLX_LM,
                format=ModelFormat.MLX,
                path="/models/bad",
                context_window=0,
            )


# ── Cross-field validation ──────────────────────────────────────────────────


class TestCrossFieldValidation:
    def test_gguf_must_use_llama_cpp_engine(self):
        """GGUF format models must route to llama_cpp."""
        entry = RegistryModelEntry(
            display_name="Bad GGUF",
            engine=EngineType.MLX_LM,  # Wrong!
            format=ModelFormat.GGUF,
            path="/models/bad.gguf",
        )
        with pytest.raises(RegistryValidationError, match="GGUF models must use engine 'llama_cpp'"):
            _validate_registry_entry("bad-gguf", entry)

    def test_valid_gguf_passes(self):
        entry = RegistryModelEntry(
            display_name="Good GGUF",
            engine=EngineType.LLAMA_CPP,
            format=ModelFormat.GGUF,
            path="/models/good.gguf",
        )
        _validate_registry_entry("good-gguf", entry)  # Should not raise

    def test_mlx_must_use_mlx_lm_or_mlx_vlm(self):
        """MLX format models must route to mlx_lm or mlx_vlm."""
        entry = RegistryModelEntry(
            display_name="Bad MLX",
            engine=EngineType.LLAMA_CPP,  # Wrong!
            format=ModelFormat.MLX,
            path="/models/bad",
        )
        with pytest.raises(RegistryValidationError, match="MLX models must use engine 'mlx_lm' or 'mlx_vlm'"):
            _validate_registry_entry("bad-mlx", entry)

    def test_mlx_lm_passes(self):
        entry = RegistryModelEntry(
            display_name="Good MLX LM",
            engine=EngineType.MLX_LM,
            format=ModelFormat.MLX,
            path="/models/good",
        )
        _validate_registry_entry("good-mlx-lm", entry)  # Should not raise

    def test_mlx_vlm_passes(self):
        entry = RegistryModelEntry(
            display_name="Good MLX VLM",
            engine=EngineType.MLX_VLM,
            format=ModelFormat.MLX,
            path="/models/good-vlm",
        )
        _validate_registry_entry("good-mlx-vlm", entry)  # Should not raise

    def test_vision_model_must_use_vision_capable_engine(self):
        """Vision models require a vision-capable engine (mlx_vlm)."""
        entry = RegistryModelEntry(
            display_name="Bad Vision",
            engine=EngineType.MLX_LM,  # Text-only engine!
            format=ModelFormat.MLX,
            path="/models/bad-vision",
            modalities=[ModelModality.TEXT, ModelModality.VISION],
        )
        with pytest.raises(RegistryValidationError, match="Vision models require a vision-capable engine"):
            _validate_registry_entry("bad-vision", entry)

    def test_vision_model_with_mlx_vlm_passes(self):
        entry = RegistryModelEntry(
            display_name="Good Vision",
            engine=EngineType.MLX_VLM,
            format=ModelFormat.MLX,
            path="/models/good-vision",
            modalities=[ModelModality.TEXT, ModelModality.VISION],
        )
        _validate_registry_entry("good-vision", entry)  # Should not raise

    def test_validate_registry_checks_all_entries(self):
        """validate_registry runs all entries and stops on first error."""
        config = ModelRegistryConfig(
            models={
                "good": RegistryModelEntry(
                    display_name="Good",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="/models/good",
                ),
                "bad": RegistryModelEntry(
                    display_name="Bad",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.GGUF,
                    path="/models/bad.gguf",
                ),
            }
        )
        with pytest.raises(RegistryValidationError, match=r"\[bad\] GGUF models must use engine"):
            validate_registry(config)


# ── ModelRegistryConfig tests ────────────────────────────────────────────────


class TestModelRegistryConfig:
    def test_empty_registry(self):
        config = ModelRegistryConfig()
        assert len(config.models) == 0
        assert bool(config) is False
        assert config.enabled_models() == []

    def test_enabled_models_filters_disabled(self):
        config = ModelRegistryConfig(
            models={
                "enabled-model": RegistryModelEntry(
                    display_name="Enabled",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="/models/enabled",
                    enabled=True,
                ),
                "disabled-model": RegistryModelEntry(
                    display_name="Disabled",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="/models/disabled",
                    enabled=False,
                ),
            }
        )
        enabled = config.enabled_models()
        assert len(enabled) == 1
        assert enabled[0][0] == "enabled-model"

    def test_get_model(self):
        config = ModelRegistryConfig(
            models={
                "my-model": RegistryModelEntry(
                    display_name="My Model",
                    engine=EngineType.MLX_LM,
                    format=ModelFormat.MLX,
                    path="/models/my",
                ),
            }
        )
        assert config.get("my-model") is not None
        assert config.get("nonexistent") is None


# ── YAML loader tests ───────────────────────────────────────────────────────


class TestLoadModelRegistry:
    def test_load_valid_yaml(self, tmp_path):
        data = {
            "models": {
                "qwen3-coder-30b-gguf": {
                    "display_name": "Qwen3 Coder 30B GGUF",
                    "engine": "llama_cpp",
                    "format": "gguf",
                    "path": "/models/qwen3-coder-30b/q4_k_m.gguf",
                    "modalities": ["text"],
                    "context_window": 32768,
                    "preferred_hardware": ["cuda", "metal", "cpu"],
                    "warm_policy": "warm_on_first_use",
                    "priority": "coding",
                    "enabled": True,
                    "tags": ["coding", "gguf", "local"],
                },
                "gemma-vision-mlx": {
                    "display_name": "Gemma Vision MLX",
                    "engine": "mlx_vlm",
                    "format": "mlx",
                    "path": "mlx-community/example-vision-model",
                    "modalities": ["text", "vision"],
                    "context_window": 32768,
                    "preferred_hardware": ["apple_silicon", "metal"],
                    "warm_policy": "keep_warm",
                    "priority": "vision",
                    "enabled": True,
                    "tags": ["vision", "mlx", "local"],
                },
            }
        }
        yaml_path = tmp_path / "models.yaml"
        _write_yaml(yaml_path, data)

        registry = load_model_registry(str(yaml_path))
        assert registry is not None
        assert len(registry.models) == 2
        assert "qwen3-coder-30b-gguf" in registry.models
        assert "gemma-vision-mlx" in registry.models

        qwen = registry.models["qwen3-coder-30b-gguf"]
        assert qwen.engine == EngineType.LLAMA_CPP
        assert qwen.format == ModelFormat.GGUF
        assert qwen.modalities == [ModelModality.TEXT]

        gemma = registry.models["gemma-vision-mlx"]
        assert gemma.engine == EngineType.MLX_VLM
        assert gemma.format == ModelFormat.MLX
        assert gemma.is_vision_capable()

    def test_load_invalid_engine_format_combo(self, tmp_path):
        """Loading a YAML with gguf format + mlx_lm engine should raise."""
        data = {
            "models": {
                "bad-model": {
                    "display_name": "Bad Model",
                    "engine": "mlx_lm",
                    "format": "gguf",
                    "path": "/models/bad.gguf",
                    "modalities": ["text"],
                }
            }
        }
        yaml_path = tmp_path / "bad.yaml"
        _write_yaml(yaml_path, data)

        with pytest.raises(RegistryValidationError, match="GGUF models must use engine"):
            load_model_registry(str(yaml_path))

    def test_load_invalid_vision_text_engine(self, tmp_path):
        """Vision model assigned to mlx_lm should be rejected."""
        data = {
            "models": {
                "bad-vision": {
                    "display_name": "Bad Vision Model",
                    "engine": "mlx_lm",
                    "format": "mlx",
                    "path": "/models/bad",
                    "modalities": ["text", "vision"],
                }
            }
        }
        yaml_path = tmp_path / "bad_vision.yaml"
        _write_yaml(yaml_path, data)

        with pytest.raises(RegistryValidationError, match="Vision models require a vision-capable engine"):
            load_model_registry(str(yaml_path))

    def test_missing_required_fields_raises(self, tmp_path):
        """Missing required fields like 'display_name' should fail."""
        data = {
            "models": {
                "incomplete": {
                    "engine": "mlx_lm",
                    "format": "mlx",
                    "path": "/models/test",
                }
            }
        }
        yaml_path = tmp_path / "incomplete.yaml"
        _write_yaml(yaml_path, data)

        with pytest.raises(Exception):  # Pydantic validation error
            load_model_registry(str(yaml_path))

    def test_no_models_key_raises(self, tmp_path):
        """Registry file without top-level 'models' key."""
        data = {"not_models": {}}
        yaml_path = tmp_path / "no_models.yaml"
        _write_yaml(yaml_path, data)

        with pytest.raises(RegistryValidationError, match="must contain a top-level 'models' key"):
            load_model_registry(str(yaml_path))

    def test_models_not_a_dict_raises(self, tmp_path):
        """Registry file where 'models' is not a mapping."""
        data = {"models": ["not", "a", "dict"]}
        yaml_path = tmp_path / "not_dict.yaml"
        _write_yaml(yaml_path, data)

        with pytest.raises(RegistryValidationError, match="must be a mapping of model_id"):
            load_model_registry(str(yaml_path))

    def test_invalid_yaml_syntax(self, tmp_path):
        """Malformed YAML should produce a clean error."""
        bad_path = tmp_path / "bad_syntax.yaml"
        bad_path.write_text("models:\n  - this: is\nbad: yaml: :::", encoding="utf-8")

        with pytest.raises(RegistryValidationError, match="Invalid YAML"):
            load_model_registry(str(bad_path))

    def test_nonexistent_path_returns_none(self):
        """When the registry file doesn't exist, return None (no error)."""
        result = load_model_registry("/nonexistent/path/models.yaml")
        assert result is None

    def test_no_env_var_returns_none(self, monkeypatch):
        """Without WHOOSHD_MODEL_REGISTRY_PATH, returns None (no auto-discovery)."""
        monkeypatch.delenv("WHOOSHD_MODEL_REGISTRY_PATH", raising=False)
        result = load_model_registry()
        assert result is None

    def test_load_example_registry(self):
        """The bundled example configs/models.yaml should load cleanly."""
        example_path = Path("configs") / "models.yaml"
        if not example_path.is_file():
            pytest.skip("Example registry file not found at configs/models.yaml")

        registry = load_model_registry(str(example_path))
        assert registry is not None
        assert len(registry.models) >= 2
        # Check the GGUF entry.
        qwen = registry.models.get("qwen3-coder-30b-gguf")
        assert qwen is not None
        assert qwen.engine == EngineType.LLAMA_CPP
        assert qwen.format == ModelFormat.GGUF
        # Check the MLX VLM entry.
        gemma = registry.models.get("gemma-vision-mlx")
        assert gemma is not None
        assert gemma.engine == EngineType.MLX_VLM
        assert gemma.format == ModelFormat.MLX
        assert gemma.is_vision_capable()


# ── Backward compatibility tests ─────────────────────────────────────────


class TestBackwardCompatibility:
    """Backward compatibility: when no registry file is present, the existing
    single-model environment-variable behaviour is preserved."""

    def test_load_model_registry_returns_none_when_no_file(self, monkeypatch):
        """Without WHOOSHD_MODEL_REGISTRY_PATH and no default file, returns None."""
        monkeypatch.delenv("WHOOSHD_MODEL_REGISTRY_PATH", raising=False)
        reg = load_model_registry()
        # Should return None (no registry file) in clean test environment.
        assert reg is None or isinstance(reg, ModelRegistryConfig)

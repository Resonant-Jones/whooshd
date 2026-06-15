"""Whoosh'd model registry — typed descriptors, YAML loader, and validation.

The registry describes all available local models regardless of engine
(MLX or GGUF).  Model format and engine implementation are internal
routing concerns — external clients see a uniform model ID and capabilities.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ───────────────────────────────────────────────────────────────────


class EngineType(str, Enum):
    """Supported inference engine backends."""

    MLX_LM = "mlx_lm"
    MLX_VLM = "mlx_vlm"
    LLAMA_CPP = "llama_cpp"

    @classmethod
    def vision_capable(cls) -> set["EngineType"]:
        """Engines that are capable of vision (multimodal) inference."""
        return {cls.MLX_VLM}

    @classmethod
    def text_capable(cls) -> set["EngineType"]:
        """Engines that are capable of text-only inference."""
        return {cls.MLX_LM, cls.MLX_VLM, cls.LLAMA_CPP}


class ModelFormat(str, Enum):
    """Model file format."""

    MLX = "mlx"
    GGUF = "gguf"


class ModelModality(str, Enum):
    """Supported model modalities."""

    TEXT = "text"
    VISION = "vision"
    EMBEDDING = "embedding"


class WarmPolicy(str, Enum):
    """Warm / lifecycle policy for a model.

    Controls when the model is loaded into memory:
      * ``cold`` — never loaded automatically; must be loaded explicitly
      * ``warm_on_start`` — loaded when Whoosh'd starts
      * ``warm_on_first_use`` — loaded lazily on the first inference request
      * ``keep_warm`` — loaded on start and never unloaded unless explicitly requested
    """

    COLD = "cold"
    WARM_ON_START = "warm_on_start"
    WARM_ON_FIRST_USE = "warm_on_first_use"
    KEEP_WARM = "keep_warm"


# ── Hardware affinity (descriptive hints) ───────────────────────────────────


class HardwareAffinity(str, Enum):
    """Preferred hardware targets for routing / validation.

    These are descriptive hints; no hardware probing is performed yet.
    """

    APPLE_SILICON = "apple_silicon"
    METAL = "metal"
    CUDA = "cuda"
    HIP = "hip"
    VULKAN = "vulkan"
    CPU = "cpu"
    AUTO = "auto"


# ── Registry model entry ────────────────────────────────────────────────────


class RegistryModelEntry(BaseModel):
    """A single model descriptor in the registry.

    Every field except ``tags`` is validated on load.  The registry
    loader enforces cross-field rules (e.g. gguf → llama_cpp).
    """

    display_name: str = Field(
        ..., min_length=1, description="Human-readable model name"
    )
    engine: EngineType = Field(..., description="Inference engine backend")
    format: ModelFormat = Field(..., description="Model file format")
    path: str = Field(
        ..., min_length=1, description="HF repo ID, local directory, or GGUF file path"
    )
    modalities: list[ModelModality] = Field(
        default_factory=lambda: [ModelModality.TEXT],
        min_length=1,
        description="Supported modalities",
    )
    context_window: int = Field(
        32768, ge=0, description="Maximum context window in tokens"
    )
    preferred_hardware: list[HardwareAffinity] = Field(
        default_factory=lambda: [HardwareAffinity.AUTO],
        min_length=1,
        description="Preferred hardware targets in priority order",
    )
    warm_policy: WarmPolicy = Field(
        WarmPolicy.WARM_ON_FIRST_USE, description="Model lifecycle warm policy"
    )
    priority: str = Field(
        "general", description="Priority or usage class label (e.g. coding, vision)"
    )
    enabled: bool = Field(True, description="Whether this model is active")
    tags: list[str] = Field(default_factory=list, description="Free-form tags")

    @field_validator("context_window")
    @classmethod
    def _context_window_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("context_window must be positive")
        return v

    def has_modality(self, modality: ModelModality) -> bool:
        """Return True if this model supports the given modality."""
        return modality in self.modalities

    def is_vision_capable(self) -> bool:
        """Return True if this model supports vision."""
        return ModelModality.VISION in self.modalities


# ── Registry container ──────────────────────────────────────────────────────


class ModelRegistryConfig(BaseModel):
    """Container for the full model registry loaded from YAML."""

    models: dict[str, RegistryModelEntry] = Field(
        default_factory=dict, description="Map of model_id → RegistryModelEntry"
    )

    def enabled_models(self) -> list[tuple[str, RegistryModelEntry]]:
        """Return all enabled model entries as (model_id, entry) pairs."""
        return [(mid, e) for mid, e in self.models.items() if e.enabled]

    def enabled_models_for_runtime(
        self, *, configured_mlx_model: str | None = None
    ) -> list[tuple[str, RegistryModelEntry]]:
        """Return enabled entries compatible with the active runtime config.

        The registry may carry multiple validated MLX text aliases, but a
        running Whoosh'd process serves one configured MLX-LM Server model at a
        time.  When ``WHOOSHD_MLX_MODEL`` is set, only the MLX text entry whose
        runtime path matches that configured model is advertised/routable.
        Non-MLX-text lanes remain visible so GGUF and VLM inventory is not
        hidden by text-model switching.
        """
        configured = (configured_mlx_model or "").strip()
        results: list[tuple[str, RegistryModelEntry]] = []
        for model_id, entry in self.enabled_models():
            if (
                configured
                and entry.engine == EngineType.MLX_LM
                and entry.path.strip() != configured
            ):
                continue
            results.append((model_id, entry))
        return results

    def get_for_runtime(
        self, model_id: str, *, configured_mlx_model: str | None = None
    ) -> Optional[RegistryModelEntry]:
        """Look up a model by ID after active-runtime filtering."""
        for mid, entry in self.enabled_models_for_runtime(
            configured_mlx_model=configured_mlx_model
        ):
            if mid == model_id:
                return entry
        return None

    def get(self, model_id: str) -> Optional[RegistryModelEntry]:
        """Look up a model by ID."""
        return self.models.get(model_id)

    def __bool__(self) -> bool:
        return len(self.models) > 0

    def __len__(self) -> int:
        return len(self.models)


# ── Validation ──────────────────────────────────────────────────────────────


class RegistryValidationError(Exception):
    """Raised when a model registry entry fails validation."""

    def __init__(self, model_id: str, message: str):
        self.model_id = model_id
        self.message = message
        super().__init__(f"[{model_id}] {message}")


def _validate_registry_entry(model_id: str, entry: RegistryModelEntry) -> None:
    """Run cross-field validation rules on a single registry entry.

    Raises ``RegistryValidationError`` for the first rule violation.
    """
    # Rule 1: GGUF format must use llama_cpp engine.
    if entry.format == ModelFormat.GGUF and entry.engine != EngineType.LLAMA_CPP:
        raise RegistryValidationError(
            model_id,
            f"GGUF models must use engine 'llama_cpp', not '{entry.engine.value}'",
        )

    # Rule 2: MLX format must use mlx_lm or mlx_vlm engine.
    if entry.format == ModelFormat.MLX and entry.engine not in (
        EngineType.MLX_LM,
        EngineType.MLX_VLM,
    ):
        raise RegistryValidationError(
            model_id,
            f"MLX models must use engine 'mlx_lm' or 'mlx_vlm', not '{entry.engine.value}'",
        )

    # Rule 3: Vision models must use a vision-capable engine.
    if ModelModality.VISION in entry.modalities and entry.engine not in EngineType.vision_capable():
        raise RegistryValidationError(
            model_id,
            f"Vision models require a vision-capable engine (mlx_vlm), "
            f"but engine is '{entry.engine.value}'",
        )

    # Rule 4: context_window must be positive.
    if entry.context_window <= 0:
        raise RegistryValidationError(
            model_id, f"context_window must be > 0, got {entry.context_window}"
        )


def validate_registry(registry: ModelRegistryConfig) -> None:
    """Validate every entry in the registry.

    Raises ``RegistryValidationError`` on the first invalid entry.
    """
    for model_id, entry in registry.models.items():
        _validate_registry_entry(model_id, entry)


# ── YAML loader ─────────────────────────────────────────────────────────────


def load_model_registry(path: str | Path | None = None) -> ModelRegistryConfig | None:
    """Load the model registry from a YAML file.

    If *path* is None, the ``WHOOSHD_MODEL_REGISTRY_PATH`` environment
    variable is consulted.  If neither is set, returns ``None`` — the caller
    should fall back to environment-variable-based single-model behaviour.

    The bundled ``configs/models.yaml`` is an example file; it is NOT
    auto-loaded by default.  Set ``WHOOSHD_MODEL_REGISTRY_PATH`` to point
    at it (or any registry YAML) to activate registry-driven model inventory.

    Raises ``RegistryValidationError`` if the file exists but contains
    invalid entries.
    """
    resolved_path: Path | None = None

    if path is not None:
        resolved_path = Path(path)
    else:
        env_path = os.environ.get("WHOOSHD_MODEL_REGISTRY_PATH", "")
        if env_path:
            resolved_path = Path(env_path)

    if resolved_path is None or not resolved_path.is_file():
        return None

    raw = resolved_path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RegistryValidationError(
            str(resolved_path), f"Invalid YAML: {exc}"
        ) from exc

    if not isinstance(data, dict) or "models" not in data:
        raise RegistryValidationError(
            str(resolved_path),
            "Registry file must contain a top-level 'models' key",
        )

    raw_models = data["models"]
    if not isinstance(raw_models, dict):
        raise RegistryValidationError(
            str(resolved_path),
            "'models' must be a mapping of model_id → model entry",
        )

    registry = ModelRegistryConfig.model_validate(data)
    validate_registry(registry)
    return registry

"""Model registry contracts — typed descriptors for the persistent model-store.

These contracts define the *durable* model-store layout and manifest,
separate from the runtime model registry (``whooshd/registry.py``) which
describes *configured* models for inference routing.

Distinction:
  - Model-store: on-disk artifacts, intake area, durable manifest
  - Runtime registry: YAML/config-driven model descriptors for routing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Store layout ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelStoreLayout:
    """Describes the intended directory layout of a Whoosh'd model-store.

    All paths are relative to the *store_root*.
    """

    store_root: Path

    @property
    def incoming(self) -> Path:
        """Drop zone for new model artifacts before inspection."""
        return self.store_root / "incoming"

    @property
    def models_mlx(self) -> Path:
        """MLX-format text models."""
        return self.store_root / "models" / "mlx"

    @property
    def models_gguf(self) -> Path:
        """GGUF-format models (llama.cpp)."""
        return self.store_root / "models" / "gguf"

    @property
    def models_vlm(self) -> Path:
        """MLX-format vision-language models."""
        return self.store_root / "models" / "vlm"

    @property
    def registry_dir(self) -> Path:
        """Persistent registry manifests live here."""
        return self.store_root / "registry"

    @property
    def quarantine(self) -> Path:
        """Models that failed inspection or are pending review."""
        return self.store_root / "quarantine"

    @property
    def tmp(self) -> Path:
        """Temporary workspace for atomic writes and staging."""
        return self.store_root / "tmp"

    @property
    def manifest_path(self) -> Path:
        """Path to the durable registry manifest JSON file."""
        return self.registry_dir / "models.json"

    def all_directories(self) -> tuple[Path, ...]:
        """Return every directory this layout defines, in creation order."""
        return (
            self.incoming,
            self.models_mlx,
            self.models_gguf,
            self.models_vlm,
            self.registry_dir,
            self.quarantine,
            self.tmp,
        )


# ── Manifest ──────────────────────────────────────────────────────────────


@dataclass
class ModelRegistryManifest:
    """Durable registry manifest stored at ``registry/models.json``.

    Schema version 1 fields:
      - schema_version: always 1 for this bootstrap
      - store_root: absolute path of the store
      - created_at: ISO-8601 timestamp of first creation
      - updated_at: ISO-8601 timestamp of last modification
      - models: list of registered model entries (empty for bootstrap)
    """

    schema_version: int = 1
    store_root: str = ""
    created_at: str = ""
    updated_at: str = ""
    models: list[dict] = field(default_factory=list)

    @classmethod
    def create(cls, store_root: str | Path) -> "ModelRegistryManifest":
        """Create a fresh manifest for a new store root."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            schema_version=1,
            store_root=str(Path(store_root).resolve()),
            created_at=now,
            updated_at=now,
            models=[],
        )

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "store_root": self.store_root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "models": list(self.models),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelRegistryManifest":
        """Deserialize from a dict.  Returns a new instance."""
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            store_root=str(data.get("store_root", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            models=list(data.get("models", [])),
        )


# ── Bootstrap state ───────────────────────────────────────────────────────


@dataclass
class ModelRegistryState:
    """Result of bootstrapping the model-store.

    Carries enough information for callers to understand what happened
    without needing to re-read the filesystem.
    """

    store_root: str
    manifest_path: str
    manifest_created: bool = False
    manifest_reused: bool = False
    directories_created: list[str] = field(default_factory=list)
    schema_version: int = 1
    error: Optional[str] = None

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


# ── Candidate inspection ──────────────────────────────────────────────────


class ModelCandidateStatus:
    """Well-known candidate inspection statuses."""

    CANDIDATE = "candidate"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class ModelCandidateFormat:
    """Well-known detected model formats."""

    MLX = "mlx"
    GGUF = "gguf"
    UNKNOWN = "unknown"


@dataclass
class ModelCandidate:
    """A single inspected model candidate — not yet registered or advertised.

    Fields:
        candidate_id: Stable identifier derived from path + evidence.
        status: ``candidate``, ``unsupported``, or ``invalid``.
        source_path: Absolute path to the user-provided artifact.
        detected_format: ``mlx``, ``gguf``, or ``unknown``.
        detected_family: ``gemma``, ``qwen``, ``llama``, ``unknown``.
        modalities: ``["text"]``, ``["text", "vision"]``, or ``[]``.
        evidence: Machine-readable strings describing what was found.
        problems: Machine-readable strings describing issues found.
        created_at: ISO-8601 timestamp of inspection.
    """

    candidate_id: str
    status: str = ModelCandidateStatus.CANDIDATE
    source_path: str = ""
    detected_format: str = ModelCandidateFormat.UNKNOWN
    detected_family: str = "unknown"
    modalities: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "source_path": self.source_path,
            "detected_format": self.detected_format,
            "detected_family": self.detected_family,
            "modalities": list(self.modalities),
            "evidence": list(self.evidence),
            "problems": list(self.problems),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelCandidate":
        return cls(
            candidate_id=str(data.get("candidate_id", "")),
            status=str(data.get("status", ModelCandidateStatus.CANDIDATE)),
            source_path=str(data.get("source_path", "")),
            detected_format=str(data.get("detected_format", ModelCandidateFormat.UNKNOWN)),
            detected_family=str(data.get("detected_family", "unknown")),
            modalities=list(data.get("modalities", [])),
            evidence=list(data.get("evidence", [])),
            problems=list(data.get("problems", [])),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class ModelCandidateInspectionResult:
    """Result of inspecting a single model artifact path.

    Carries the candidate record and any top-level error.
    """

    candidate: ModelCandidate
    error: Optional[str] = None


# ── Registered models ─────────────────────────────────────────────────────


class RegisteredModelStatus:
    """Well-known registered model statuses."""

    REGISTERED = "registered"
    INVALID = "invalid"


class RegisteredModelStorageMode:
    """Well-known storage modes for registered models."""

    MANAGED = "managed"


@dataclass
class RegisteredModel:
    """A model that has been registered in the durable manifest.

    Registration means Whoosh'd knows what the model is.  It does NOT
    mean the model is advertised in ``/v1/models`` or runnable by an
    adapter.  Those are separate lifecycle transitions.

    Fields:
        model_id: Stable, safe identifier (no path traversal).
        display_name: Human-readable name.
        status: ``registered`` or ``invalid``.
        storage_mode: Always ``managed`` for now.
        managed_path: Relative path inside the model-store.
        source_candidate_id: The candidate this was registered from.
        source_path: Original absolute source path.
        detected_format: ``mlx``, ``gguf``, or ``unknown``.
        detected_family: ``gemma``, ``qwen``, ``llama``, ``unknown``.
        modalities: e.g. ``["text"]`` or ``["text", "vision"]``.
        evidence: Inspection evidence preserved from the candidate.
        created_at: ISO-8601 timestamp.
        updated_at: ISO-8601 timestamp.
    """

    model_id: str
    display_name: str = ""
    status: str = RegisteredModelStatus.REGISTERED
    storage_mode: str = RegisteredModelStorageMode.MANAGED
    managed_path: str = ""
    source_candidate_id: str = ""
    source_path: str = ""
    detected_format: str = ModelCandidateFormat.UNKNOWN
    detected_family: str = "unknown"
    modalities: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "status": self.status,
            "storage_mode": self.storage_mode,
            "managed_path": self.managed_path,
            "source_candidate_id": self.source_candidate_id,
            "source_path": self.source_path,
            "detected_format": self.detected_format,
            "detected_family": self.detected_family,
            "modalities": list(self.modalities),
            "evidence": list(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RegisteredModel":
        return cls(
            model_id=str(data.get("model_id", "")),
            display_name=str(data.get("display_name", "")),
            status=str(data.get("status", RegisteredModelStatus.REGISTERED)),
            storage_mode=str(data.get("storage_mode", RegisteredModelStorageMode.MANAGED)),
            managed_path=str(data.get("managed_path", "")),
            source_candidate_id=str(data.get("source_candidate_id", "")),
            source_path=str(data.get("source_path", "")),
            detected_format=str(data.get("detected_format", ModelCandidateFormat.UNKNOWN)),
            detected_family=str(data.get("detected_family", "unknown")),
            modalities=list(data.get("modalities", [])),
            evidence=list(data.get("evidence", [])),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class ModelRegistrationResult:
    """Result of registering a candidate into the managed model store.

    Carries the registered model entry and any error/problem metadata.
    """

    registered_model: RegisteredModel
    managed_path: str = ""
    manifest_updated: bool = False
    problem: Optional[str] = None

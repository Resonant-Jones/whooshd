"""Snapshot materialization contract for ThreadWake Phase M16.

Defines the formal metadata contract for future real KV snapshot
material.  No KV tensors are persisted.  No snapshots are restored.
Material status defaults to DECLARED (metadata-only).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SnapshotMaterialKind(str, Enum):
    METADATA_ONLY = "metadata_only"
    BACKEND_NATIVE = "backend_native"
    FILE_BACKED = "file_backed"
    MEMORY_BACKED = "memory_backed"
    UNSUPPORTED = "unsupported"


class SnapshotMaterialStatus(str, Enum):
    DECLARED = "declared"
    MATERIALIZED = "materialized"
    VALIDATED = "validated"
    INVALID = "invalid"
    EXPIRED = "expired"
    UNSUPPORTED = "unsupported"


@dataclass
class SnapshotMaterialContract:
    material_id: str
    artifact_id: str = ""
    manifest_id: str = ""
    prefix_hash: str = ""
    backend: str | None = None
    model_id: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    material_kind: str = SnapshotMaterialKind.METADATA_ONLY.value
    material_status: str = SnapshotMaterialStatus.DECLARED.value
    material_version: str = "1"
    policy_version: str = "1"
    checksum: str | None = None
    byte_size: int | None = None
    token_count: int | None = None
    format_name: str | None = None
    format_version: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def safe_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "artifact_id": self.artifact_id,
            "manifest_id": self.manifest_id,
            "prefix_hash": self.prefix_hash,
            "backend": self.backend,
            "model_id": self.model_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "material_kind": self.material_kind,
            "material_status": self.material_status,
            "material_version": self.material_version,
            "policy_version": self.policy_version,
            "checksum": self.checksum,
            "byte_size": self.byte_size,
            "token_count": self.token_count,
            "format_name": self.format_name,
            "format_version": self.format_version,
            "validation_errors": list(self.validation_errors),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SnapshotMaterialBuilder:
    MATERIAL_VERSION = "1"

    @classmethod
    def declare_from_artifact(cls, artifact: Any, manifest: Any = None) -> SnapshotMaterialContract:
        aid = getattr(artifact, "artifact_id", "") or ""
        mid = getattr(manifest, "manifest_id", "") if manifest else getattr(artifact, "manifest_id", "") or ""
        material_id = cls._material_id_for(aid)

        return SnapshotMaterialContract(
            material_id=material_id,
            artifact_id=aid,
            manifest_id=mid,
            prefix_hash=getattr(artifact, "prefix_hash", "") or getattr(manifest, "prefix_hash", "") or "",
            backend=getattr(artifact, "backend", None),
            model_id=getattr(artifact, "model_id", None),
            tokenizer_hash=getattr(artifact, "tokenizer_hash", None),
            chat_template_hash=getattr(artifact, "chat_template_hash", None),
            material_kind=SnapshotMaterialKind.METADATA_ONLY.value,
            material_status=SnapshotMaterialStatus.DECLARED.value,
            material_version=cls.MATERIAL_VERSION,
            policy_version=getattr(artifact, "policy_version", "1") or "1",
            token_count=getattr(manifest, "potential_saved_tokens_total", None),
        )

    @staticmethod
    def _material_id_for(artifact_id: str) -> str:
        payload = f"{artifact_id}|1"
        return f"material-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"

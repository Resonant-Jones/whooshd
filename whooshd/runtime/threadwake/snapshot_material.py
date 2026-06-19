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


class SnapshotMaterialValidationReason(str, Enum):
    VALID_METADATA_ONLY = "valid_metadata_only"
    MISSING_MATERIAL_ID = "missing_material_id"
    MISSING_ARTIFACT_ID = "missing_artifact_id"
    MISSING_MANIFEST_ID = "missing_manifest_id"
    MISSING_PREFIX_HASH = "missing_prefix_hash"
    MISSING_BACKEND = "missing_backend"
    MISSING_MODEL_ID = "missing_model_id"
    MISSING_TOKENIZER_HASH = "missing_tokenizer_hash"
    MISSING_CHAT_TEMPLATE_HASH = "missing_chat_template_hash"
    INVALID_MATERIAL_KIND = "invalid_material_kind"
    INVALID_MATERIAL_STATUS = "invalid_material_status"
    MATERIALIZED_WITHOUT_CHECKSUM = "materialized_without_checksum"
    MATERIALIZED_WITHOUT_BYTE_SIZE = "materialized_without_byte_size"
    VALIDATED_WITHOUT_MATERIALIZATION = "validated_without_materialization"
    UNSUPPORTED_BACKEND = "unsupported_backend"
    RAW_CONTENT_DETECTED = "raw_content_detected"
    UNSAFE_REFERENCE_DETECTED = "unsafe_reference_detected"


@dataclass
class SnapshotMaterialValidationResult:
    valid: bool = False
    reason: str = ""
    material_id: str | None = None
    material_kind: str | None = None
    material_status: str | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    errors: list[str] = field(default_factory=list)

    def safe_dict(self) -> dict:
        return {"valid": self.valid, "reason": self.reason, "material_id": self.material_id,
                "material_kind": self.material_kind, "material_status": self.material_status,
                "checked_at": self.checked_at, "errors": list(self.errors)}


class SnapshotMaterialValidator:
    """Validates SnapshotMaterialContract metadata for structural safety."""

    REQUIRED = ["material_id","artifact_id","manifest_id","prefix_hash","backend","model_id",
                "tokenizer_hash","chat_template_hash","material_version","policy_version"]
    SUPPORTED_BACKENDS = {"mlx", "fake"}

    def __init__(self):
        self._stats = {"validations_total":0,"valid_total":0,"invalid_total":0,"last_reason":None}

    def validate(self, material: Any) -> SnapshotMaterialValidationResult:
        self._stats["validations_total"] += 1
        if material is None:
            return self._fail(SnapshotMaterialValidationReason.MISSING_MATERIAL_ID.value, [])
        errors: list[str] = []
        for f in self.REQUIRED:
            v = getattr(material, f, None)
            if not v:
                errors.append(f"missing_{f}")
        if errors:
            return self._fail(errors[0].replace("missing_", "missing_"), errors)
        kind = getattr(material, "material_kind", "")
        status = getattr(material, "material_status", "")
        if kind not in {e.value for e in SnapshotMaterialKind}:
            return self._fail(SnapshotMaterialValidationReason.INVALID_MATERIAL_KIND.value, errors)
        if status not in {e.value for e in SnapshotMaterialStatus}:
            return self._fail(SnapshotMaterialValidationReason.INVALID_MATERIAL_STATUS.value, errors)
        if status == SnapshotMaterialStatus.MATERIALIZED.value:
            if not getattr(material, "checksum", None):
                return self._fail(SnapshotMaterialValidationReason.MATERIALIZED_WITHOUT_CHECKSUM.value, errors)
            if not getattr(material, "byte_size", None) or getattr(material, "byte_size", 0) <= 0:
                return self._fail(SnapshotMaterialValidationReason.MATERIALIZED_WITHOUT_BYTE_SIZE.value, errors)
        if status == SnapshotMaterialStatus.VALIDATED.value:
            return self._fail(SnapshotMaterialValidationReason.VALIDATED_WITHOUT_MATERIALIZATION.value, errors)
        backend = getattr(material, "backend", "")
        if backend and backend not in self.SUPPORTED_BACKENDS:
            return self._fail(SnapshotMaterialValidationReason.UNSUPPORTED_BACKEND.value, errors)
        self._stats["valid_total"] += 1
        self._stats["last_reason"] = SnapshotMaterialValidationReason.VALID_METADATA_ONLY.value
        return SnapshotMaterialValidationResult(
            valid=True, reason=SnapshotMaterialValidationReason.VALID_METADATA_ONLY.value,
            material_id=getattr(material,"material_id",None), material_kind=kind, material_status=status,
        )

    def validate_many(self, materials: list) -> list:
        return [self.validate(m) for m in materials]

    def stats(self) -> dict:
        return dict(self._stats)

    def _fail(self, reason: str, errors: list) -> SnapshotMaterialValidationResult:
        self._stats["invalid_total"] += 1
        self._stats["last_reason"] = reason
        return SnapshotMaterialValidationResult(valid=False, reason=reason, errors=errors)


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

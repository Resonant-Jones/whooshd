"""Snapshot artifact layer for ThreadWake Phase M14.

Defines artifact records that represent future snapshot objects.
Artifacts are metadata only — no KV tensors, no backend state,
no snapshot restore.

Artifact lifecycle: PLANNED → BUILD_PENDING → READY (or BUILD_FAILED)
"READY" means "metadata exists and passed validation" — NOT "KV exists."
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Status ─────────────────────────────────────────────────────────────────


class SnapshotArtifactStatus(str, Enum):
    PLANNED = "planned"
    BUILD_PENDING = "build_pending"
    BUILD_FAILED = "build_failed"
    READY = "ready"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


# ── Artifact ───────────────────────────────────────────────────────────────


@dataclass
class SnapshotArtifact:
    artifact_id: str
    manifest_id: str = ""
    prefix_hash: str = ""
    backend: str | None = None
    model_id: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    status: str = SnapshotArtifactStatus.PLANNED.value
    policy_version: str = "1"
    artifact_version: str = "1"
    build_attempts: int = 0
    notes: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at: str | None = None

    def safe_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "manifest_id": self.manifest_id,
            "prefix_hash": self.prefix_hash,
            "backend": self.backend,
            "model_id": self.model_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "status": self.status,
            "policy_version": self.policy_version,
            "artifact_version": self.artifact_version,
            "build_attempts": self.build_attempts,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
        }


# ── Builder ────────────────────────────────────────────────────────────────


class SnapshotArtifactBuilder:
    ARTIFACT_VERSION = "1"

    @classmethod
    def build_from_manifest(cls, manifest: Any) -> SnapshotArtifact:
        mid = getattr(manifest, "manifest_id", "")
        artifact_id = cls._artifact_id_for(mid)

        return SnapshotArtifact(
            artifact_id=artifact_id,
            manifest_id=mid,
            prefix_hash=getattr(manifest, "prefix_hash", ""),
            backend=getattr(manifest, "backend", None),
            model_id=getattr(manifest, "model_id", None),
            tokenizer_hash=getattr(manifest, "tokenizer_hash", None),
            chat_template_hash=getattr(manifest, "chat_template_hash", None),
            status=SnapshotArtifactStatus.PLANNED.value,
            policy_version=getattr(manifest, "policy_version", "1") or "1",
            artifact_version=cls.ARTIFACT_VERSION,
        )

    @staticmethod
    def _artifact_id_for(manifest_id: str) -> str:
        payload = f"{manifest_id}|1"
        return f"artifact-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


# ── Registry ───────────────────────────────────────────────────────────────


@dataclass
class _RegistryStats:
    total: int = 0
    planned: int = 0
    build_pending: int = 0
    build_failed: int = 0
    ready: int = 0
    superseded: int = 0
    expired: int = 0

    def to_dict(self) -> dict:
        return {
            "total_artifacts": self.total, "planned": self.planned,
            "build_pending": self.build_pending, "build_failed": self.build_failed,
            "ready": self.ready, "superseded": self.superseded,
            "expired": self.expired,
        }


class SnapshotArtifactRegistry:
    """In-memory artifact registry.  Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._artifacts: dict[str, SnapshotArtifact] = {}

    def register_artifact(self, artifact: SnapshotArtifact) -> None:
        with self._lock:
            existing = self._artifacts.get(artifact.artifact_id)
            if existing:
                existing.status = artifact.status
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                existing.notes = artifact.notes or existing.notes
                existing.build_attempts += 1
            else:
                self._artifacts[artifact.artifact_id] = artifact

    def get_artifact(self, artifact_id: str) -> SnapshotArtifact | None:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def list_artifacts(
        self, limit: int = 50, status: str | None = None, backend: str | None = None,
    ) -> list[dict]:
        limit = max(1, min(limit, 500))
        with self._lock:
            items = list(self._artifacts.values())
        if status:
            items = [a for a in items if a.status == status]
        if backend:
            items = [a for a in items if a.backend == backend]
        items.sort(key=lambda a: a.created_at, reverse=True)
        return [a.safe_dict() for a in items[:limit]]

    def artifact_stats(self) -> dict:
        with self._lock:
            items = list(self._artifacts.values())
        s = _RegistryStats(total=len(items))
        for a in items:
            if a.status == "planned": s.planned += 1
            elif a.status == "build_pending": s.build_pending += 1
            elif a.status == "build_failed": s.build_failed += 1
            elif a.status == "ready": s.ready += 1
            elif a.status == "superseded": s.superseded += 1
            elif a.status == "expired": s.expired += 1
        return s.to_dict()

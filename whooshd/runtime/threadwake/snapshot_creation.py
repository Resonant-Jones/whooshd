"""Experimental snapshot creation for ThreadWake Phase M15.

Provides a gated snapshot creation path that may create snapshot records
from eligible artifacts.  Disabled by default.  Never restores, injects,
or reuses KV state.  Metadata-only until a backend exposes safe KV
snapshot extraction.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────────────────────────


class SnapshotCreationStatus(str, Enum):
    DISABLED = "disabled"
    SKIPPED = "skipped"
    CREATED = "created"
    FAILED = "failed"


class SnapshotCreationReason(str, Enum):
    EXPERIMENTAL_SNAPSHOTS_DISABLED = "experimental_snapshots_disabled"
    ARTIFACT_NOT_ELIGIBLE = "artifact_not_eligible"
    UNSUPPORTED_BACKEND = "unsupported_backend"
    MISSING_MANIFEST = "missing_manifest"
    MISSING_ARTIFACT = "missing_artifact"
    BACKEND_SNAPSHOT_UNAVAILABLE = "backend_snapshot_unavailable"
    SNAPSHOT_CREATED = "snapshot_created"
    SNAPSHOT_CREATION_FAILED = "snapshot_creation_failed"


# ── Result ─────────────────────────────────────────────────────────────────


@dataclass
class SnapshotCreationResult:
    created: bool = False
    status: str = SnapshotCreationStatus.DISABLED.value
    reason: str = ""
    artifact_id: str | None = None
    manifest_id: str | None = None
    backend: str | None = None
    model_id: str | None = None
    snapshot_ref_hash: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def safe_dict(self) -> dict:
        return {
            "created": self.created,
            "status": self.status,
            "reason": self.reason,
            "artifact_id": self.artifact_id,
            "manifest_id": self.manifest_id,
            "backend": self.backend,
            "model_id": self.model_id,
            "snapshot_ref_hash": self.snapshot_ref_hash,
            "error": self.error,
            "created_at": self.created_at,
        }


# ── Creator ────────────────────────────────────────────────────────────────


class SnapshotCreator:
    """Gated snapshot creation.  Disabled by default.

    When enabled, attempts metadata-only creation.  Backend KV snapshot
    extraction is deferred until a backend exposes a safe API.
    """

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._attempts = 0
        self._created = 0
        self._skipped = 0
        self._failed = 0
        self._last_reason: str | None = None

    # ── Public ──────────────────────────────────────────────────────────

    def create_from_artifact(self, artifact: Any, manifest: Any = None) -> SnapshotCreationResult:
        self._attempts += 1

        if not self._enabled:
            return self._skip(SnapshotCreationReason.EXPERIMENTAL_SNAPSHOTS_DISABLED.value, artifact, manifest)

        if artifact is None:
            return self._skip(SnapshotCreationReason.MISSING_ARTIFACT.value, artifact, manifest)

        backend = getattr(artifact, "backend", None) or ""
        if backend != "mlx":
            return self._skip(SnapshotCreationReason.UNSUPPORTED_BACKEND.value, artifact, manifest)

        # All real backends currently lack safe KV snapshot extraction
        return self._skip(SnapshotCreationReason.BACKEND_SNAPSHOT_UNAVAILABLE.value, artifact, manifest)

    def can_create(self, artifact: Any) -> bool:
        """Return True only if creation is possible for this artifact."""
        if not self._enabled:
            return False
        if artifact is None:
            return False
        # No backend exposes safe KV snapshot extraction yet
        return False

    # ── Stats ───────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "creation_attempts": self._attempts,
            "created": self._created,
            "skipped": self._skipped,
            "failed": self._failed,
            "last_reason": self._last_reason,
        }

    # ── Internal ────────────────────────────────────────────────────────

    def _skip(self, reason: str, artifact: Any, manifest: Any = None) -> SnapshotCreationResult:
        self._skipped += 1
        self._last_reason = reason
        return SnapshotCreationResult(
            created=False,
            status=SnapshotCreationStatus.SKIPPED.value if self._enabled else SnapshotCreationStatus.DISABLED.value,
            reason=reason,
            artifact_id=getattr(artifact, "artifact_id", None) if artifact else None,
            manifest_id=getattr(manifest, "manifest_id", None) if manifest else None,
            backend=getattr(artifact, "backend", None) if artifact else None,
            model_id=getattr(artifact, "model_id", None) if artifact else None,
        )


# ── Fake creator (test-only) ──────────────────────────────────────────────


class FakeSnapshotCreator(SnapshotCreator):
    """Test-only snapshot creator that produces metadata-only creation events."""

    def create_from_artifact(self, artifact: Any, manifest: Any = None) -> SnapshotCreationResult:
        self._attempts += 1

        if not self._enabled:
            return self._skip(SnapshotCreationReason.EXPERIMENTAL_SNAPSHOTS_DISABLED.value, artifact, manifest)
        if artifact is None:
            return self._skip(SnapshotCreationReason.MISSING_ARTIFACT.value, artifact, manifest)

        # Fake creation: produce a synthetic ref hash
        aid = getattr(artifact, "artifact_id", str(uuid.uuid4()))
        ref = hashlib.sha256(f"fake-snapshot:{aid}".encode()).hexdigest()[:16]

        self._created += 1
        self._last_reason = SnapshotCreationReason.SNAPSHOT_CREATED.value
        return SnapshotCreationResult(
            created=True,
            status=SnapshotCreationStatus.CREATED.value,
            reason=SnapshotCreationReason.SNAPSHOT_CREATED.value,
            artifact_id=getattr(artifact, "artifact_id", None),
            manifest_id=getattr(artifact, "manifest_id", None),
            backend=getattr(artifact, "backend", None),
            model_id=getattr(artifact, "model_id", None),
            snapshot_ref_hash=ref,
        )

    def can_create(self, artifact: Any) -> bool:
        return self._enabled and artifact is not None

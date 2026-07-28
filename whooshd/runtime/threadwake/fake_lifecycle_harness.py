"""M27 fake/test backend lifecycle harness for ThreadWake.

This module is intentionally test-only and metadata-only. It wires the
candidate -> manifest -> artifact -> material -> validation chain using
the existing safe contracts, but it never calls backend KV methods or
creates real KV state.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

from .artifacts import SnapshotArtifact, SnapshotArtifactBuilder, SnapshotArtifactRegistry
from .backend import FakeKVBackend
from .policy import SnapshotEligibility, SnapshotPolicyConfig, SnapshotPolicyEngine
from .replay_analysis import CandidateReplayRecord
from .snapshot_manifest import SnapshotManifest, SnapshotManifestBuilder
from .snapshot_material import (
    SnapshotMaterialBuilder,
    SnapshotMaterialContract,
    SnapshotMaterialValidationResult,
    SnapshotMaterialValidator,
)
from .storage import NoOpThreadWakeStorage, ThreadWakeStorageProtocol


def _safe_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Normalize stats for safe result serialization."""
    return {key: (0 if value is None else value) for key, value in stats.items()}


def _candidate_storage_entry(record: CandidateReplayRecord, eligibility: SnapshotEligibility) -> Any:
    """Build a storage-compatible candidate row from a replay record."""
    return SimpleNamespace(
        prompt_prefix_hash=record.prefix_hash,
        backend=record.backend or "fake",
        model_id=record.model_id or "",
        tokenizer_hash=record.tokenizer_hash,
        chat_template_hash=record.chat_template_hash,
        candidate_score=record.average_candidate_score,
        candidate_confidence=record.confidence,
        potential_saved_tokens=record.potential_saved_tokens_total,
        potential_saved_ratio=record.average_potential_saved_ratio,
        selection_reason=eligibility.reason,
    )


@dataclass
class FakeThreadWakeLifecycleResult:
    """Safe result for the fake/test lifecycle harness."""

    backend_capability: str = "unsupported"
    candidate: CandidateReplayRecord | None = None
    eligibility: SnapshotEligibility | None = None
    manifest: SnapshotManifest | None = None
    artifact: SnapshotArtifact | None = None
    material: SnapshotMaterialContract | None = None
    validation: SnapshotMaterialValidationResult | None = None
    stage_order: list[str] = field(default_factory=list)
    storage_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    validation_recorded: bool = False
    completed: bool = False
    valid: bool = False
    terminal_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "backend_capability": self.backend_capability,
            "candidate": self.candidate.safe_dict() if self.candidate else None,
            "eligibility": self.eligibility.safe_dict() if self.eligibility else None,
            "manifest": self.manifest.safe_dict() if self.manifest else None,
            "artifact": self.artifact.safe_dict() if self.artifact else None,
            "material": self.material.safe_dict() if self.material else None,
            "validation": self.validation.safe_dict() if self.validation else None,
            "stage_order": list(self.stage_order),
            "storage_stats": {
                name: dict(stats) for name, stats in self.storage_stats.items()
            },
            "validation_recorded": self.validation_recorded,
            "completed": self.completed,
            "valid": self.valid,
            "terminal_reason": self.terminal_reason,
            "notes": list(self.notes),
        }


class FakeThreadWakeLifecycleHarness:
    """Test-only metadata harness for the fake ThreadWake backend path.

    The harness is deliberately narrow:
    - accepts fake/test candidate telemetry only
    - persists metadata through the existing safe storage adapters
    - never invokes KV prefill, clone, generate, or release methods
    """

    def __init__(
        self,
        *,
        backend: FakeKVBackend | None = None,
        storage: ThreadWakeStorageProtocol | None = None,
        artifact_registry: SnapshotArtifactRegistry | None = None,
        policy_engine: SnapshotPolicyEngine | None = None,
        validator: SnapshotMaterialValidator | None = None,
    ) -> None:
        self._backend = backend or FakeKVBackend()
        self._backend_capability = self._backend.supports_kv_cache().value
        self._storage = storage or NoOpThreadWakeStorage()
        self._artifact_registry = artifact_registry or SnapshotArtifactRegistry()
        self._policy_engine = policy_engine or SnapshotPolicyEngine(
            SnapshotPolicyConfig(supported_backends={"fake"})
        )
        self._validator = validator or SnapshotMaterialValidator()

    def run(self, record: CandidateReplayRecord) -> FakeThreadWakeLifecycleResult:
        """Run the fake lifecycle flow for a single replay record."""
        result = FakeThreadWakeLifecycleResult(
            backend_capability=self._backend_capability,
            candidate=record,
        )

        candidate = record if record.backend == "fake" else replace(record, backend="fake")
        if record.backend not in (None, "", "fake"):
            result.terminal_reason = "backend_not_fake"
            result.notes.append("fake_backend_required")
            result.storage_stats = self._snapshot_storage_stats()
            return result

        result.candidate = candidate
        eligibility = self._policy_engine.evaluate_candidate(candidate)
        result.eligibility = eligibility
        self._storage.upsert_candidate(_candidate_storage_entry(candidate, eligibility))
        result.stage_order.append("candidate")

        if not eligibility.eligible:
            result.terminal_reason = eligibility.reason or "candidate_ineligible"
            result.notes.append(f"candidate_rejected:{result.terminal_reason}")
            result.storage_stats = self._snapshot_storage_stats()
            return result

        result.stage_order.append("manifest")
        manifest = SnapshotManifestBuilder.build_from_replay_record(candidate, eligibility)
        result.manifest = manifest
        if manifest is None:
            result.terminal_reason = "manifest_build_failed"
            result.notes.append("manifest_build_failed")
            result.storage_stats = self._snapshot_storage_stats()
            return result
        self._storage.upsert_snapshot_manifest(manifest)

        result.stage_order.append("artifact")
        artifact = SnapshotArtifactBuilder.build_from_manifest(manifest)
        result.artifact = artifact
        self._artifact_registry.register_artifact(artifact)
        self._storage.upsert_snapshot_artifact(artifact)

        result.stage_order.append("material")
        material = SnapshotMaterialBuilder.declare_from_artifact(artifact, manifest)
        result.material = material
        self._storage.upsert_snapshot_material(material)

        result.stage_order.append("validation")
        validation = self._validator.validate(material)
        result.validation = validation
        self._storage.record_snapshot_material_validation(validation)
        result.validation_recorded = True

        result.completed = True
        result.valid = validation.valid
        result.storage_stats = self._snapshot_storage_stats()
        if validation.valid:
            result.notes.append("validation_passed")
        else:
            result.notes.append(f"validation_failed:{validation.reason}")
        return result

    def _snapshot_storage_stats(self) -> dict[str, dict[str, Any]]:
        return {
            "candidates": _safe_stats(self._storage.candidate_stats()),
            "manifests": _safe_stats(self._storage.snapshot_manifest_stats()),
            "artifacts": _safe_stats(self._storage.snapshot_artifact_stats()),
            "materials": _safe_stats(self._storage.snapshot_material_stats()),
        }

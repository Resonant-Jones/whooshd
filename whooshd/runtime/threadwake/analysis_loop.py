"""Metadata-only periodic analysis loop for ThreadWake Phase M20.

Wires SnapshotPolicyEngine, SnapshotManifestBuilder, and
SnapshotArtifactRegistry into a safe analysis pipeline that reads
observed candidate telemetry and emits metadata-only manifests and
artifacts.  No KV tensors are created, persisted, restored, or reused.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Result ─────────────────────────────────────────────────────────────────


@dataclass
class AnalysisLoopResult:
    candidates_scanned: int = 0
    candidates_eligible: int = 0
    manifests_created: int = 0
    artifacts_registered: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def safe_dict(self) -> dict:
        return {
            "candidates_scanned": self.candidates_scanned,
            "candidates_eligible": self.candidates_eligible,
            "manifests_created": self.manifests_created,
            "artifacts_registered": self.artifacts_registered,
            "skipped": self.skipped,
            "errors": self.errors,
        }


# ── Coordinator ────────────────────────────────────────────────────────────


class ThreadWakeAnalysisLoop:
    """Periodic metadata-only analysis loop.

    Reads candidate telemetry from the ThreadWakeIndex, evaluates
    eligibility via SnapshotPolicyEngine, builds manifests and
    registers artifacts.  No KV state is mutated.  No inference
    path is touched.
    """

    def __init__(
        self,
        index: Any = None,
        storage: Any = None,
        artifact_registry: Any = None,
    ) -> None:
        self._index = index
        self._storage = storage
        self._artifact_registry = artifact_registry
        self._run_count = 0
        self._last_result: AnalysisLoopResult | None = None

    # ── Public ──────────────────────────────────────────────────────────

    def run(self, limit: int = 50) -> AnalysisLoopResult:
        """Execute one pass of the analysis loop."""
        result = AnalysisLoopResult()

        try:
            from .replay_analysis import CandidateReplayAnalyzer
            from .policy import SnapshotPolicyEngine
            from .snapshot_manifest import SnapshotManifestBuilder
            from .artifacts import SnapshotArtifactBuilder

            analyzer = CandidateReplayAnalyzer()
            policy_engine = SnapshotPolicyEngine()

            # Read candidates from index (in-memory, safe)
            if self._index is None:
                result.details.append("no_index_available")
                return result

            summary = analyzer.analyze_index(self._index, limit=limit)
            result.candidates_scanned = summary.total_candidates

            for record in summary.top_candidates:
                try:
                    eligibility = policy_engine.evaluate_candidate(record)
                    if not eligibility.eligible:
                        result.skipped += 1
                        continue

                    result.candidates_eligible += 1

                    # Build manifest
                    manifest = SnapshotManifestBuilder.build_from_replay_record(record, eligibility)
                    if manifest is None:
                        result.errors += 1
                        result.details.append("manifest_build_failed")
                        continue

                    result.manifests_created += 1

                    # Persist manifest to optional storage
                    if self._storage is not None:
                        try:
                            self._storage.upsert_snapshot_manifest(manifest)
                        except Exception:
                            pass  # Best-effort; never fail the loop

                    # Build and register artifact
                    if self._artifact_registry is not None:
                        try:
                            artifact = SnapshotArtifactBuilder.build_from_manifest(manifest)
                            self._artifact_registry.register_artifact(artifact)
                            result.artifacts_registered += 1

                            if self._storage is not None:
                                try:
                                    self._storage.upsert_snapshot_artifact(artifact)
                                except Exception:
                                    pass
                        except Exception:
                            result.errors += 1
                            result.details.append("artifact_registration_failed")

                except Exception:
                    result.errors += 1

        except Exception as exc:
            logger.warning("ThreadWake analysis loop error: %s", exc)
            result.errors += 1
            result.details.append(str(exc))
        finally:
            self._run_count += 1
            self._last_result = result

        return result

    def last_result(self) -> dict | None:
        if self._last_result is None:
            return None
        return self._last_result.safe_dict()

    @property
    def run_count(self) -> int:
        return self._run_count

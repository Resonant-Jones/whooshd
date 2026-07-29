"""Tests for the M27 fake/test ThreadWake lifecycle harness."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from whooshd.runtime.threadwake.backend import FakeKVBackend
from whooshd.runtime.threadwake.fake_lifecycle_harness import FakeThreadWakeLifecycleHarness
from whooshd.runtime.threadwake.replay_analysis import CandidateReplayRecord
from whooshd.runtime.threadwake.storage import SQLiteThreadWakeStorage
from whooshd.runtime.threadwake.artifacts import SnapshotArtifactRegistry


def _record(**overrides):
    defaults = {
        "prefix_hash": "fake-prefix-001",
        "backend": "fake",
        "model_id": "fake-model",
        "tokenizer_hash": "tok-001",
        "chat_template_hash": "tmpl-001",
        "seen_count": 12,
        "potential_saved_tokens_total": 3200,
        "average_candidate_score": 0.95,
        "average_potential_saved_ratio": 0.75,
        "confidence": "high",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(overrides)
    return CandidateReplayRecord(**defaults)


class TestFakeThreadWakeLifecycleHarness:
    def test_end_to_end_metadata_flow(self, tmp_path):
        storage = SQLiteThreadWakeStorage(str(tmp_path / "threadwake.sqlite3"))
        registry = SnapshotArtifactRegistry()
        backend = FakeKVBackend()
        harness = FakeThreadWakeLifecycleHarness(
            backend=backend,
            storage=storage,
            artifact_registry=registry,
        )

        result = harness.run(_record())

        assert result.completed is True
        assert result.valid is True
        assert result.terminal_reason is None
        assert result.stage_order == ["candidate", "manifest", "artifact", "material", "validation"]
        assert result.backend_capability == "resumable"
        assert result.validation_recorded is True
        assert result.candidate is not None
        assert result.eligibility is not None
        assert result.manifest is not None
        assert result.artifact is not None
        assert result.material is not None
        assert result.validation is not None
        assert result.validation.valid is True
        assert result.manifest.backend == "fake"
        assert result.artifact.backend == "fake"
        assert result.material.backend == "fake"
        assert result.storage_stats["candidates"]["total_candidates"] == 1
        assert result.storage_stats["manifests"]["total_manifests"] == 1
        assert result.storage_stats["artifacts"]["total_artifacts"] == 1
        assert result.storage_stats["materials"]["total_materials"] == 1
        assert registry.artifact_stats()["total_artifacts"] == 1
        assert storage.candidate_stats()["total_candidates"] == 1
        assert storage.snapshot_manifest_stats()["total_manifests"] == 1
        assert storage.snapshot_artifact_stats()["total_artifacts"] == 1
        assert storage.snapshot_material_stats()["total_materials"] == 1
        validation_rows = storage._conn.execute(
            "SELECT COUNT(*) FROM threadwake_snapshot_material_validation_events"
        ).fetchone()
        assert validation_rows is not None
        assert validation_rows[0] == 1
        safe = result.safe_dict()
        safe_json = json.dumps(safe)
        assert "token_ids" not in safe_json
        assert "opaque_ref" not in safe_json
        assert backend.prefill_calls == []
        assert backend.generate_from_kv_calls == []
        assert backend.clone_calls == 0
        assert backend.release_calls == 0
        assert backend._store == {}
        storage.close()

    def test_ineligible_candidate_stops_before_manifest(self, tmp_path):
        storage = SQLiteThreadWakeStorage(str(tmp_path / "threadwake.sqlite3"))
        backend = FakeKVBackend()
        harness = FakeThreadWakeLifecycleHarness(backend=backend, storage=storage)

        result = harness.run(_record(seen_count=1, average_candidate_score=0.2))

        assert result.completed is False
        assert result.valid is False
        assert result.terminal_reason == "insufficient_observations"
        assert result.stage_order == ["candidate"]
        assert result.manifest is None
        assert result.artifact is None
        assert result.material is None
        assert result.validation is None
        assert result.storage_stats["candidates"]["total_candidates"] == 1
        assert result.storage_stats["manifests"]["total_manifests"] == 0
        assert result.storage_stats["artifacts"]["total_artifacts"] == 0
        assert result.storage_stats["materials"]["total_materials"] == 0
        assert backend.prefill_calls == []
        assert backend._store == {}
        storage.close()

    def test_non_fake_backend_is_rejected(self, tmp_path):
        storage = SQLiteThreadWakeStorage(str(tmp_path / "threadwake.sqlite3"))
        backend = FakeKVBackend()
        harness = FakeThreadWakeLifecycleHarness(backend=backend, storage=storage)

        result = harness.run(_record(backend="mlx"))

        assert result.completed is False
        assert result.valid is False
        assert result.terminal_reason == "backend_not_fake"
        assert result.stage_order == []
        assert result.manifest is None
        assert result.artifact is None
        assert result.material is None
        assert result.validation is None
        assert result.storage_stats["candidates"]["total_candidates"] == 0
        assert backend.prefill_calls == []
        assert backend.generate_from_kv_calls == []
        assert backend._store == {}
        storage.close()

"""Tests for snapshot artifact layer."""

from __future__ import annotations

import json
import os
import tempfile

from whooshd.runtime.threadwake.artifacts import (
    SnapshotArtifact,
    SnapshotArtifactBuilder,
    SnapshotArtifactRegistry,
    SnapshotArtifactStatus,
)
from whooshd.runtime.threadwake.snapshot_manifest import SnapshotManifest, SnapshotManifestBuilder
from whooshd.runtime.threadwake.policy import SnapshotEligibility
from whooshd.runtime.threadwake.replay_analysis import CandidateReplayRecord
from whooshd.runtime.threadwake.storage import NoOpThreadWakeStorage, SQLiteThreadWakeStorage


def _record():
    return CandidateReplayRecord(
        prefix_hash="abc", backend="mlx", model_id="m",
        tokenizer_hash="tok", chat_template_hash="tmpl",
        seen_count=10, average_candidate_score=0.9,
        average_potential_saved_ratio=0.75,
        potential_saved_tokens_total=3000, confidence="high",
    )


def _manifest():
    eligibility = SnapshotEligibility(eligible=True, reason="eligible")
    return SnapshotManifestBuilder.build_from_replay_record(_record(), eligibility)


# ── Builder ───────────────────────────────────────────────────────────────


class TestBuilder:
    def test_builds_from_manifest(self):
        m = _manifest()
        a = SnapshotArtifactBuilder.build_from_manifest(m)
        assert a.manifest_id == m.manifest_id
        assert a.status == SnapshotArtifactStatus.PLANNED.value
        assert a.artifact_version == "1"

    def test_artifact_id_deterministic(self):
        m = _manifest()
        a1 = SnapshotArtifactBuilder.build_from_manifest(m)
        a2 = SnapshotArtifactBuilder.build_from_manifest(m)
        assert a1.artifact_id == a2.artifact_id

    def test_different_manifest_different_id(self):
        m1 = _manifest()
        r2 = CandidateReplayRecord(prefix_hash="xyz", backend="mlx", model_id="m",
                                    tokenizer_hash="tok", chat_template_hash="tmpl",
                                    seen_count=10, average_candidate_score=0.9,
                                    average_potential_saved_ratio=0.5,
                                    potential_saved_tokens_total=1000, confidence="high")
        e = SnapshotEligibility(eligible=True)
        m2 = SnapshotManifestBuilder.build_from_replay_record(r2, e)
        a1 = SnapshotArtifactBuilder.build_from_manifest(m1)
        a2 = SnapshotArtifactBuilder.build_from_manifest(m2)
        assert a1.artifact_id != a2.artifact_id


# ── Registry ──────────────────────────────────────────────────────────────


class TestRegistry:
    def test_register_and_retrieve(self):
        reg = SnapshotArtifactRegistry()
        a = SnapshotArtifact(artifact_id="a1", manifest_id="m1", prefix_hash="h")
        reg.register_artifact(a)
        assert reg.get_artifact("a1") is not None

    def test_reregister_updates(self):
        reg = SnapshotArtifactRegistry()
        a1 = SnapshotArtifact(artifact_id="a1", status="planned")
        a2 = SnapshotArtifact(artifact_id="a1", status="ready")
        reg.register_artifact(a1)
        reg.register_artifact(a2)
        assert reg.get_artifact("a1").status == "ready"

    def test_filters_by_status(self):
        reg = SnapshotArtifactRegistry()
        reg.register_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h", status="planned"))
        reg.register_artifact(SnapshotArtifact(artifact_id="b", prefix_hash="h2", status="ready"))
        planned = reg.list_artifacts(status="planned")
        assert len(planned) == 1

    def test_filters_by_backend(self):
        reg = SnapshotArtifactRegistry()
        reg.register_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h", backend="mlx"))
        reg.register_artifact(SnapshotArtifact(artifact_id="b", prefix_hash="h2", backend="fake"))
        mlx = reg.list_artifacts(backend="mlx")
        assert len(mlx) == 1

    def test_artifact_stats(self):
        reg = SnapshotArtifactRegistry()
        reg.register_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h", status="planned"))
        reg.register_artifact(SnapshotArtifact(artifact_id="b", prefix_hash="h2", status="ready"))
        stats = reg.artifact_stats()
        assert stats["total_artifacts"] == 2
        assert stats["planned"] == 1
        assert stats["ready"] == 1


# ── Privacy ───────────────────────────────────────────────────────────────


class TestPrivacy:
    def test_safe_dict_no_raw_content(self):
        a = SnapshotArtifact(artifact_id="a1", prefix_hash="h")
        d = a.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)
        assert "user_id" not in json.dumps(d)


# ── SQLite ────────────────────────────────────────────────────────────────


class TestSQLite:
    @staticmethod
    def _db():
        fd, p = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(p), p

    def test_artifact_table_exists(self):
        s, p = self._db()
        rows = s._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threadwake_snapshot_artifacts'").fetchall()
        assert len(rows) == 1
        s.close(); os.unlink(p)

    def test_upsert_inserts(self):
        s, p = self._db()
        a = SnapshotArtifact(artifact_id="a1", manifest_id="m1", prefix_hash="h", status="planned")
        s.upsert_snapshot_artifact(a)
        rows = s.list_snapshot_artifacts()
        assert len(rows) == 1
        s.close(); os.unlink(p)

    def test_upsert_updates(self):
        s, p = self._db()
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="a1", prefix_hash="h", status="planned"))
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="a1", prefix_hash="h", status="ready"))
        rows = s.list_snapshot_artifacts()
        assert rows[0]["status"] == "ready"
        s.close(); os.unlink(p)

    def test_filters_status(self):
        s, p = self._db()
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h", status="planned"))
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="b", prefix_hash="h2", status="ready"))
        assert len(s.list_snapshot_artifacts(status="planned")) == 1
        s.close(); os.unlink(p)

    def test_stats(self):
        s, p = self._db()
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h", status="planned"))
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="b", prefix_hash="h2", status="ready"))
        st = s.snapshot_artifact_stats()
        assert st["total_artifacts"] == 2
        s.close(); os.unlink(p)

    def test_noop_storage_safe(self):
        s = NoOpThreadWakeStorage()
        a = SnapshotArtifact(artifact_id="a", prefix_hash="h")
        s.upsert_snapshot_artifact(a)
        assert s.list_snapshot_artifacts() == []
        assert s.snapshot_artifact_stats()["total_artifacts"] == 0

    def test_error_does_not_crash(self):
        s, p = self._db()
        s.close()
        s.upsert_snapshot_artifact(SnapshotArtifact(artifact_id="a", prefix_hash="h"))
        os.unlink(p)

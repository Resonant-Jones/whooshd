"""Tests for snapshot manifest system."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from whooshd.runtime.threadwake.policy import SnapshotEligibility, SnapshotPolicyEngine
from whooshd.runtime.threadwake.replay_analysis import CandidateReplayRecord
from whooshd.runtime.threadwake.snapshot_manifest import (
    SnapshotManifest,
    SnapshotManifestBuilder,
    SnapshotManifestStatus,
)
from whooshd.runtime.threadwake.storage import (
    NoOpThreadWakeStorage,
    SQLiteThreadWakeStorage,
)


def _record(**overrides):
    defaults = {
        "prefix_hash": "abc123", "backend": "mlx", "model_id": "m",
        "tokenizer_hash": "tok-001", "chat_template_hash": "tmpl-001",
        "seen_count": 10, "average_candidate_score": 0.90,
        "average_potential_saved_ratio": 0.75,
        "potential_saved_tokens_total": 3000,
        "confidence": "high",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(overrides)
    return CandidateReplayRecord(**defaults)


# ── Builder ───────────────────────────────────────────────────────────────


class TestBuilder:
    def test_build_from_eligible(self):
        engine = SnapshotPolicyEngine()
        record = _record()
        eligibility = engine.evaluate_candidate(record)
        manifest = SnapshotManifestBuilder.build_from_replay_record(record, eligibility)
        assert manifest is not None
        assert manifest.status == SnapshotManifestStatus.PLANNED.value
        assert manifest.prefix_hash == "abc123"
        assert manifest.policy_version == "1"

    def test_reject_ineligible(self):
        record = _record(seen_count=1)
        eligibility = SnapshotEligibility(eligible=False, reason="low_value")
        manifest = SnapshotManifestBuilder.build_from_replay_record(record, eligibility)
        assert manifest is None

    def test_manifest_id_deterministic(self):
        r1 = _record(prefix_hash="hash-a")
        r2 = _record(prefix_hash="hash-a")
        e = SnapshotEligibility(eligible=True)
        m1 = SnapshotManifestBuilder.build_from_replay_record(r1, e)
        m2 = SnapshotManifestBuilder.build_from_replay_record(r2, e)
        assert m1.manifest_id == m2.manifest_id

    def test_different_prefix_different_id(self):
        r1 = _record(prefix_hash="hash-a")
        r2 = _record(prefix_hash="hash-b")
        e = SnapshotEligibility(eligible=True)
        m1 = SnapshotManifestBuilder.build_from_replay_record(r1, e)
        m2 = SnapshotManifestBuilder.build_from_replay_record(r2, e)
        assert m1.manifest_id != m2.manifest_id

    def test_manifest_includes_eligibility_reason(self):
        engine = SnapshotPolicyEngine()
        record = _record()
        eligibility = engine.evaluate_candidate(record)
        manifest = SnapshotManifestBuilder.build_from_replay_record(record, eligibility)
        assert manifest.eligibility_reason == "high_frequency_high_savings"


class TestSafeDict:
    def test_no_raw_content(self):
        engine = SnapshotPolicyEngine()
        m = SnapshotManifestBuilder.build_from_replay_record(
            _record(), engine.evaluate_candidate(_record()))
        assert m is not None
        d = m.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)
        assert "user_id" not in json.dumps(d)
        assert "thread_id" not in json.dumps(d)

    def test_json_serializable(self):
        m = SnapshotManifest(manifest_id="m1", prefix_hash="h")
        json.dumps(m.safe_dict())


# ── Storage ───────────────────────────────────────────────────────────────


class TestNoOpStorage:
    def test_noop_handles_manifest_calls(self):
        s = NoOpThreadWakeStorage()
        m = SnapshotManifest(manifest_id="m1", prefix_hash="h")
        s.upsert_snapshot_manifest(m)  # Should not raise
        assert s.list_snapshot_manifests() == []
        stats = s.snapshot_manifest_stats()
        assert stats["total_manifests"] == 0


class TestSQLiteStorage:
    @staticmethod
    def _make_db():
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(path), path

    def test_manifest_table_exists(self):
        s, path = self._make_db()
        rows = s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threadwake_snapshot_manifests'"
        ).fetchall()
        assert len(rows) == 1
        s.close()
        os.unlink(path)

    def test_upsert_inserts(self):
        s, path = self._make_db()
        m = SnapshotManifest(manifest_id="m-1", prefix_hash="h1", backend="mlx",
                             status="planned")
        s.upsert_snapshot_manifest(m)
        rows = s.list_snapshot_manifests()
        assert len(rows) == 1
        assert rows[0]["manifest_id"] == "m-1"
        s.close()
        os.unlink(path)

    def test_upsert_updates(self):
        s, path = self._make_db()
        m1 = SnapshotManifest(manifest_id="m-2", prefix_hash="h2",
                              candidate_score=0.5, status="planned")
        m2 = SnapshotManifest(manifest_id="m-2", prefix_hash="h2",
                              candidate_score=0.9, status="superseded")
        s.upsert_snapshot_manifest(m1)
        s.upsert_snapshot_manifest(m2)
        rows = s.list_snapshot_manifests()
        assert rows[0]["candidate_score"] == 0.9
        assert rows[0]["status"] == "superseded"
        s.close()
        os.unlink(path)

    def test_filters_by_status(self):
        s, path = self._make_db()
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="a", prefix_hash="h", status="planned"))
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="b", prefix_hash="h2", status="expired"))
        planned = s.list_snapshot_manifests(status="planned")
        assert len(planned) == 1
        assert planned[0]["manifest_id"] == "a"
        s.close()
        os.unlink(path)

    def test_filters_by_backend(self):
        s, path = self._make_db()
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="a", prefix_hash="h", backend="mlx"))
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="b", prefix_hash="h2", backend="fake"))
        mlx = s.list_snapshot_manifests(backend="mlx")
        assert len(mlx) == 1
        assert mlx[0]["backend"] == "mlx"
        s.close()
        os.unlink(path)

    def test_manifest_stats(self):
        s, path = self._make_db()
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="a", prefix_hash="h", status="planned"))
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="b", prefix_hash="h2", status="planned"))
        s.upsert_snapshot_manifest(SnapshotManifest(manifest_id="c", prefix_hash="h3", status="expired"))
        stats = s.snapshot_manifest_stats()
        assert stats["total_manifests"] == 3
        assert stats["planned"] == 2
        assert stats["expired"] == 1
        s.close()
        os.unlink(path)

    def test_persistence_error_does_not_crash(self):
        s, path = self._make_db()
        s.close()
        m = SnapshotManifest(manifest_id="m", prefix_hash="h")
        s.upsert_snapshot_manifest(m)  # Should not raise
        os.unlink(path)

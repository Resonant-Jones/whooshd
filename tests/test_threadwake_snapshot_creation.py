"""Tests for experimental snapshot creation."""

from __future__ import annotations

import json
import os
import tempfile

from whooshd.runtime.threadwake.artifacts import SnapshotArtifact
from whooshd.runtime.threadwake.snapshot_creation import (
    FakeSnapshotCreator,
    SnapshotCreationReason,
    SnapshotCreationStatus,
    SnapshotCreator,
)
from whooshd.runtime.threadwake.storage import SQLiteThreadWakeStorage


def _artifact(**kw):
    defaults = {"artifact_id": "a1", "manifest_id": "m1", "prefix_hash": "h",
                "backend": "mlx", "model_id": "m"}
    defaults.update(kw)
    return SnapshotArtifact(**defaults)


class TestDisabled:
    def test_disabled_returns_disabled(self):
        c = SnapshotCreator(enabled=False)
        r = c.create_from_artifact(_artifact())
        assert r.created is False
        assert r.status == SnapshotCreationStatus.DISABLED.value
        assert r.reason == SnapshotCreationReason.EXPERIMENTAL_SNAPSHOTS_DISABLED.value

    def test_can_create_false_when_disabled(self):
        assert SnapshotCreator(enabled=False).can_create(_artifact()) is False


class TestSkipReasons:
    def test_unsupported_backend_skips(self):
        c = SnapshotCreator(enabled=True)
        r = c.create_from_artifact(_artifact(backend="llama_cpp"))
        assert r.status == SnapshotCreationStatus.SKIPPED.value
        assert r.reason == SnapshotCreationReason.UNSUPPORTED_BACKEND.value

    def test_missing_artifact_skips(self):
        c = SnapshotCreator(enabled=True)
        r = c.create_from_artifact(None)
        assert r.reason == SnapshotCreationReason.MISSING_ARTIFACT.value

    def test_backend_unavailable_skips(self):
        c = SnapshotCreator(enabled=True)
        r = c.create_from_artifact(_artifact(backend="mlx"))
        assert r.reason == SnapshotCreationReason.BACKEND_SNAPSHOT_UNAVAILABLE.value

    def test_mlx_not_supported_yet(self):
        c = SnapshotCreator(enabled=True)
        assert c.can_create(_artifact(backend="mlx")) is False


class TestFakeCreator:
    def test_fake_creates(self):
        c = FakeSnapshotCreator(enabled=True)
        r = c.create_from_artifact(_artifact())
        assert r.created is True
        assert r.status == SnapshotCreationStatus.CREATED.value
        assert r.snapshot_ref_hash is not None

    def test_fake_disabled_returns_disabled(self):
        c = FakeSnapshotCreator(enabled=False)
        r = c.create_from_artifact(_artifact())
        assert r.created is False

    def test_fake_can_create(self):
        assert FakeSnapshotCreator(enabled=True).can_create(_artifact()) is True


class TestPrivacy:
    def test_safe_dict_no_raw_content(self):
        c = FakeSnapshotCreator(enabled=True)
        r = c.create_from_artifact(_artifact())
        d = r.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)
        assert "user_id" not in json.dumps(d)


class TestStats:
    def test_tracks_attempts(self):
        c = SnapshotCreator(enabled=True)
        c.create_from_artifact(_artifact())
        s = c.stats()
        assert s["creation_attempts"] == 1
        assert s["skipped"] == 1

    def test_fake_tracks_created(self):
        c = FakeSnapshotCreator(enabled=True)
        c.create_from_artifact(_artifact())
        s = c.stats()
        assert s["created"] == 1


class TestSQLite:
    @staticmethod
    def _db():
        fd, p = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(p), p

    def test_creation_events_table_exists(self):
        s, p = self._db()
        rows = s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threadwake_snapshot_creation_events'"
        ).fetchall()
        assert len(rows) == 1
        s.close(); os.unlink(p)

    def test_creation_failure_does_not_crash(self):
        s, p = self._db()
        s.close()
        c = SnapshotCreator(enabled=True)
        c.create_from_artifact(_artifact())  # Should not raise
        os.unlink(p)

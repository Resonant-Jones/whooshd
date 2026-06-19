"""Tests for snapshot material contract."""

from __future__ import annotations

import json, os, tempfile

from whooshd.runtime.threadwake.artifacts import SnapshotArtifact
from whooshd.runtime.threadwake.snapshot_material import (
    SnapshotMaterialBuilder, SnapshotMaterialContract,
    SnapshotMaterialKind, SnapshotMaterialStatus,
)
from whooshd.runtime.threadwake.storage import NoOpThreadWakeStorage, SQLiteThreadWakeStorage


def _artifact(**kw):
    d = {"artifact_id":"a1","manifest_id":"m1","prefix_hash":"h","backend":"mlx","model_id":"m"}
    d.update(kw)
    return SnapshotArtifact(**d)


class TestBuilder:
    def test_declare_from_artifact(self):
        m = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        assert m.material_status == SnapshotMaterialStatus.DECLARED.value
        assert m.material_kind == SnapshotMaterialKind.METADATA_ONLY.value
        assert m.artifact_id == "a1"

    def test_material_id_deterministic(self):
        m1 = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        m2 = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        assert m1.material_id == m2.material_id

    def test_different_artifact_different_id(self):
        m1 = SnapshotMaterialBuilder.declare_from_artifact(_artifact(artifact_id="a"))
        m2 = SnapshotMaterialBuilder.declare_from_artifact(_artifact(artifact_id="b"))
        assert m1.material_id != m2.material_id

    def test_defaults_not_materialized(self):
        m = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        assert m.checksum is None
        assert m.byte_size is None
        assert m.material_status != SnapshotMaterialStatus.MATERIALIZED.value

    def test_defaults_not_validated(self):
        m = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        assert m.material_status != SnapshotMaterialStatus.VALIDATED.value
        assert m.validation_errors == []


class TestSafeDict:
    def test_no_raw_content(self):
        m = SnapshotMaterialBuilder.declare_from_artifact(_artifact())
        d = m.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)


class TestSQLite:
    @staticmethod
    def _db():
        fd, p = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(p), p

    def test_materials_table_exists(self):
        s, p = self._db()
        rows = s._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threadwake_snapshot_materials'").fetchall()
        assert len(rows) == 1
        s.close(); os.unlink(p)

    def test_upsert_inserts(self):
        s, p = self._db()
        m = SnapshotMaterialContract(material_id="mat-1", artifact_id="a1", manifest_id="m1", prefix_hash="h", material_status="declared")
        s.upsert_snapshot_material(m)
        assert len(s.list_snapshot_materials()) == 1
        s.close(); os.unlink(p)

    def test_upsert_updates(self):
        s, p = self._db()
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="mat-1", artifact_id="a1", manifest_id="m1", prefix_hash="h", material_status="declared"))
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="mat-1", artifact_id="a1", manifest_id="m1", prefix_hash="h", material_status="validated"))
        rows = s.list_snapshot_materials()
        assert rows[0]["material_status"] == "validated"
        s.close(); os.unlink(p)

    def test_filters_status(self):
        s, p = self._db()
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="a", artifact_id="a1", manifest_id="m1", prefix_hash="h", material_status="declared"))
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="b", artifact_id="a2", manifest_id="m2", prefix_hash="h2", material_status="materialized"))
        assert len(s.list_snapshot_materials(status="declared")) == 1
        s.close(); os.unlink(p)

    def test_stats(self):
        s, p = self._db()
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="a", artifact_id="a1", manifest_id="m1", prefix_hash="h", material_status="declared"))
        s.upsert_snapshot_material(SnapshotMaterialContract(material_id="b", artifact_id="a2", manifest_id="m2", prefix_hash="h2", material_status="validated"))
        st = s.snapshot_material_stats()
        assert st["total_materials"] == 2
        s.close(); os.unlink(p)

    def test_noop_safe(self):
        s = NoOpThreadWakeStorage()
        m = SnapshotMaterialContract(material_id="mat-1", artifact_id="a1", manifest_id="m1", prefix_hash="h")
        s.upsert_snapshot_material(m)
        assert s.list_snapshot_materials() == []
        assert s.snapshot_material_stats()["total_materials"] == 0

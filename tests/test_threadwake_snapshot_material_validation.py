"""Tests for snapshot material validation."""

from __future__ import annotations

import json, os, tempfile

from whooshd.runtime.threadwake.artifacts import SnapshotArtifact
from whooshd.runtime.threadwake.snapshot_material import (
    SnapshotMaterialBuilder, SnapshotMaterialContract,
    SnapshotMaterialValidationReason, SnapshotMaterialValidator,
)
from whooshd.runtime.threadwake.storage import SQLiteThreadWakeStorage


def _material(**kw):
    m = SnapshotMaterialBuilder.declare_from_artifact(
        SnapshotArtifact(artifact_id="a1", manifest_id="m1", prefix_hash="h",
                         backend="mlx", model_id="m", tokenizer_hash="tok",
                         chat_template_hash="tmpl"))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


class TestValid:
    def test_metadata_only_declared_validates(self):
        r = SnapshotMaterialValidator().validate(_material())
        assert r.valid is True
        assert r.reason == SnapshotMaterialValidationReason.VALID_METADATA_ONLY.value

    def test_does_not_mutate_status(self):
        m = _material()
        SnapshotMaterialValidator().validate(m)
        assert m.material_status == "declared"  # Unchanged


class TestRejections:
    def test_missing_material_id(self):
        r = SnapshotMaterialValidator().validate(_material(material_id=""))
        assert r.valid is False

    def test_missing_artifact_id(self):
        r = SnapshotMaterialValidator().validate(_material(artifact_id=""))
        assert r.valid is False

    def test_missing_prefix_hash(self):
        r = SnapshotMaterialValidator().validate(_material(prefix_hash=""))
        assert r.valid is False

    def test_missing_backend(self):
        r = SnapshotMaterialValidator().validate(_material(backend=""))
        assert r.valid is False

    def test_materialized_without_checksum(self):
        r = SnapshotMaterialValidator().validate(
            _material(material_status="materialized", checksum=None, byte_size=100))
        assert r.valid is False
        assert r.reason == SnapshotMaterialValidationReason.MATERIALIZED_WITHOUT_CHECKSUM.value

    def test_materialized_without_byte_size(self):
        r = SnapshotMaterialValidator().validate(
            _material(material_status="materialized", checksum="abc", byte_size=0))
        assert r.valid is False

    def test_validated_without_materialization(self):
        r = SnapshotMaterialValidator().validate(_material(material_status="validated"))
        assert r.valid is False
        assert r.reason == SnapshotMaterialValidationReason.VALIDATED_WITHOUT_MATERIALIZATION.value

    def test_unsupported_backend(self):
        r = SnapshotMaterialValidator().validate(_material(backend="unsupported"))
        assert r.valid is False


class TestPrivacy:
    def test_safe_dict_clean(self):
        r = SnapshotMaterialValidator().validate(_material())
        d = r.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)

    def test_result_json_serializable(self):
        r = SnapshotMaterialValidator().validate(_material())
        json.dumps(r.safe_dict())


class TestStats:
    def test_tracks(self):
        v = SnapshotMaterialValidator()
        v.validate(_material())
        v.validate(_material(material_id=""))
        s = v.stats()
        assert s["validations_total"] == 2
        assert s["valid_total"] == 1
        assert s["invalid_total"] == 1


class TestSQLite:
    @staticmethod
    def _db():
        fd, p = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(p), p

    def test_validation_events_table(self):
        s, p = self._db()
        rows = s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threadwake_snapshot_material_validation_events'"
        ).fetchall()
        assert len(rows) == 1
        s.close(); os.unlink(p)

    def test_record_validation(self):
        s, p = self._db()
        r = SnapshotMaterialValidator().validate(_material())
        s.record_snapshot_material_validation(r)  # Should not raise
        s.close(); os.unlink(p)

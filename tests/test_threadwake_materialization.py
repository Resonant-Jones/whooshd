"""Tests for backend materialization interface."""

from __future__ import annotations

import json, pytest

from whooshd.runtime.threadwake.materialization import (
    MaterializationCapability, MaterializationReason,
    MaterializerRegistry, MLXSnapshotMaterializer,
    NoOpSnapshotMaterializer,
)


class TestNoOp:
    def test_reports_unsupported(self):
        m = NoOpSnapshotMaterializer()
        r = m.capability()
        assert r.capability == MaterializationCapability.UNSUPPORTED.value
        assert r.supports_materialization is False
        assert r.supports_restore is False
        assert r.supports_reuse is False

    def test_cannot_materialize(self):
        assert NoOpSnapshotMaterializer().can_materialize() is False

    def test_materialize_raises(self):
        with pytest.raises(NotImplementedError):
            NoOpSnapshotMaterializer().materialize(None, None)


class TestMLX:
    def test_reports_declared(self):
        m = MLXSnapshotMaterializer()
        r = m.capability()
        assert r.capability == MaterializationCapability.DECLARED.value
        assert r.reason == MaterializationReason.BACKEND_INTERFACE_DECLARED.value

    def test_restore_false(self):
        assert MLXSnapshotMaterializer().capability().supports_restore is False

    def test_reuse_false(self):
        assert MLXSnapshotMaterializer().capability().supports_reuse is False

    def test_materialize_raises(self):
        with pytest.raises(NotImplementedError):
            MLXSnapshotMaterializer().materialize(None, None)


class TestRegistry:
    def test_register_and_get(self):
        r = MaterializerRegistry()
        r.register(MLXSnapshotMaterializer())
        m = r.get("mlx")
        assert m.backend_name == "mlx"
        assert m.capability().capability == MaterializationCapability.DECLARED.value

    def test_unregistered_returns_noop(self):
        r = MaterializerRegistry()
        m = r.get("unknown")
        assert isinstance(m, NoOpSnapshotMaterializer)

    def test_unregister(self):
        r = MaterializerRegistry()
        r.register(MLXSnapshotMaterializer())
        r.unregister("mlx")
        assert isinstance(r.get("mlx"), NoOpSnapshotMaterializer)

    def test_list_materializers(self):
        r = MaterializerRegistry()
        r.register(MLXSnapshotMaterializer())
        assert r.list_materializers() == ["mlx"]

    def test_capability_summary(self):
        r = MaterializerRegistry()
        r.register(MLXSnapshotMaterializer())
        s = r.capability_summary()
        assert len(s) == 1
        assert s[0]["backend"] == "mlx"

    def test_stats(self):
        r = MaterializerRegistry()
        r.register(MLXSnapshotMaterializer())
        s = r.stats()
        assert s["registered_backends"] == 1
        assert s["declared"] == 1
        assert s["supported"] == 0


class TestPrivacy:
    def test_safe_dict_clean(self):
        r = MLXSnapshotMaterializer().capability()
        d = r.safe_dict()
        assert "token_ids" not in json.dumps(d)
        assert "opaque_ref" not in json.dumps(d)
        assert "prompt" not in json.dumps(d)

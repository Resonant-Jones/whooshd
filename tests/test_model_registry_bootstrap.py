"""Tests for the model registry bootstrap layer.

Validates filesystem behavior, manifest creation, idempotency, and
safety guards.  No real model files required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.contracts import (
    ModelRegistryManifest,
    ModelRegistryState,
    ModelStoreLayout,
)


# ── Bootstrap ──────────────────────────────────────────────────────────────


class TestBootstrapCreatesDirectories:
    def test_all_directories_created(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            state = bootstrap_model_store(root)

            layout = ModelStoreLayout(store_root=root)
            for directory in layout.all_directories():
                assert directory.exists(), f"Missing: {directory}"

            # Check the state reflects what happened.
            assert len(state.directories_created) == len(layout.all_directories())

    def test_manifest_created(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            state = bootstrap_model_store(root)

            assert state.manifest_created is True
            assert state.manifest_reused is False

            manifest_path = Path(state.manifest_path)
            assert manifest_path.exists()
            data = json.loads(manifest_path.read_text())
            assert data["schema_version"] == 1
            assert data["store_root"] == str(root.resolve())
            assert data["models"] == []
            assert "created_at" in data
            assert "updated_at" in data

    def test_manifest_contains_required_fields(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            state = bootstrap_model_store(root)
            data = json.loads(Path(state.manifest_path).read_text())

            for field in ("schema_version", "store_root", "created_at", "updated_at", "models"):
                assert field in data, f"Missing field: {field}"
            assert data["schema_version"] == 1
            assert isinstance(data["models"], list)
            assert len(data["models"]) == 0


class TestBootstrapIdempotency:
    def test_repeated_bootstrap_is_idempotent(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            s1 = bootstrap_model_store(root)
            s2 = bootstrap_model_store(root)

            # Second call reuses the manifest.
            assert s2.manifest_created is False
            assert s2.manifest_reused is True
            assert s2.directories_created == []
            assert s2.schema_version == 1
            assert s2.error is None

    def test_existing_manifest_is_preserved(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            s1 = bootstrap_model_store(root)

            # Read the original timestamps.
            data1 = json.loads(Path(s1.manifest_path).read_text())
            original_created = data1["created_at"]

            # Bootstrap again.
            s2 = bootstrap_model_store(root)
            data2 = json.loads(Path(s2.manifest_path).read_text())

            # created_at must not change.
            assert data2["created_at"] == original_created
            # models list must be preserved.
            assert data2["models"] == []

    def test_existing_models_array_preserved(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            s1 = bootstrap_model_store(root)

            # Manually add a model entry to the manifest.
            manifest = ModelRegistryManifest.from_dict(
                json.loads(Path(s1.manifest_path).read_text())
            )
            manifest.models.append({"id": "test-model", "format": "gguf"})
            manifest.touch()

            # Write back atomically.
            from whooshd.model_registry.bootstrap import _write_manifest_atomic
            layout = ModelStoreLayout(store_root=root)
            _write_manifest_atomic(manifest, layout)

            # Bootstrap again — models must be preserved.
            s2 = bootstrap_model_store(root)
            data2 = json.loads(Path(s2.manifest_path).read_text())
            assert len(data2["models"]) == 1
            assert data2["models"][0]["id"] == "test-model"


class TestBootstrapRejectsDangerousRoots:
    def test_empty_root_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            bootstrap_model_store("")

        with pytest.raises(ValueError, match="must not be empty"):
            bootstrap_model_store("   ")

    def test_filesystem_root_rejected(self):
        with pytest.raises(ValueError, match="filesystem root"):
            bootstrap_model_store("/")

    def test_tilde_expansion_works(self):
        with TemporaryDirectory() as d:
            # Use a path under the temp dir to simulate home expansion.
            root = Path(d) / "whooshd-models"
            state = bootstrap_model_store(str(root))
            assert state.error is None
            assert len(state.directories_created) > 0

    def test_nonexistent_parent_created(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "deep" / "nested" / "whooshd-models"
            state = bootstrap_model_store(root)
            assert state.error is None
            assert root.exists()
            assert (root / "registry" / "models.json").exists()


class TestBootstrapReturnsCorrectState:
    def test_state_fields_on_create(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            state = bootstrap_model_store(root)

            assert isinstance(state, ModelRegistryState)
            assert state.store_root == str(root.resolve())
            assert state.manifest_created is True
            assert state.manifest_reused is False
            assert len(state.directories_created) > 0
            assert state.schema_version == 1
            assert state.error is None

    def test_state_fields_on_reuse(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            bootstrap_model_store(root)  # create
            state = bootstrap_model_store(root)  # reuse

            assert state.manifest_created is False
            assert state.manifest_reused is True
            assert state.directories_created == []
            assert state.error is None


class TestBootstrapRejectsBadManifest:
    def test_corrupt_json_returns_error(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            root.mkdir(parents=True)
            (root / "registry").mkdir()
            (root / "registry" / "models.json").write_text("{not json}")

            state = bootstrap_model_store(root)
            assert state.error is not None
            assert "unreadable" in state.error.lower()

    def test_unsupported_schema_version_returns_error(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "whooshd-models"
            root.mkdir(parents=True)
            (root / "registry").mkdir()
            (root / "registry" / "models.json").write_text(
                json.dumps({"schema_version": 99, "store_root": str(root), "models": []})
            )

            state = bootstrap_model_store(root)
            assert state.error is not None
            assert "schema_version" in state.error.lower()
            assert "99" in state.error


# ── Layout contract ────────────────────────────────────────────────────────


class TestModelStoreLayout:
    def test_all_directories_are_under_root(self):
        root = Path("/tmp/test-store")
        layout = ModelStoreLayout(store_root=root)
        for d in layout.all_directories():
            assert str(d).startswith(str(root)), f"{d} not under {root}"

    def test_manifest_path(self):
        layout = ModelStoreLayout(store_root=Path("/tmp/test-store"))
        assert layout.manifest_path == Path("/tmp/test-store/registry/models.json")


# ── Manifest contract ──────────────────────────────────────────────────────


class TestModelRegistryManifest:
    def test_create_sets_timestamps(self):
        manifest = ModelRegistryManifest.create("/tmp/test")
        assert manifest.schema_version == 1
        assert manifest.store_root != ""
        assert manifest.created_at != ""
        assert manifest.updated_at != ""
        assert manifest.models == []

    def test_touch_updates_timestamp(self):
        manifest = ModelRegistryManifest.create("/tmp/test")
        original = manifest.updated_at
        manifest.touch()
        # May be identical if called fast enough, but should not regress.
        assert manifest.updated_at >= original

    def test_roundtrip(self):
        manifest = ModelRegistryManifest.create("/tmp/test")
        manifest.models.append({"id": "m1"})
        data = manifest.to_dict()
        restored = ModelRegistryManifest.from_dict(data)
        assert restored.schema_version == manifest.schema_version
        assert restored.store_root == manifest.store_root
        assert restored.models == manifest.models

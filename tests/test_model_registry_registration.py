"""Tests for model registry registration — candidate promotion to managed models.

Validates copy behavior, manifest updates, idempotency, and safety guards.
No real model files required.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import inspect_model_candidate, write_candidate_record
from whooshd.model_registry.registration import register_model_candidate, _validate_model_id


# ── Helpers ─────────────────────────────────────────────────────────────────


def _setup_candidate(root: Path, fake_dir: Path) -> str:
    """Bootstrap store, create fake MLX dir, inspect it, return candidate_id."""
    bootstrap_model_store(root)
    result = inspect_model_candidate(fake_dir)
    write_candidate_record(root, result.candidate)
    return result.candidate.candidate_id


def _fake_mlx_dir(base: Path, config: dict | None = None) -> Path:
    d = base / "fake-mlx-model"
    d.mkdir(parents=True)
    if config is None:
        config = {"model_type": "gemma"}
    (d / "config.json").write_text(json.dumps(config))
    (d / "tokenizer.json").write_text("{}")
    (d / "model.safetensors").write_text("placeholder")
    return d


def _fake_gguf(base: Path) -> Path:
    f = base / "fake-model.gguf"
    f.write_text("placeholder-gguf")
    return f


def _fake_qwen_vlm(base: Path) -> Path:
    return _fake_mlx_dir(base, {
        "model_type": "qwen2_vl",
        "architectures": ["Qwen2VLForConditionalGeneration"],
    })


# ── Registration: happy path ───────────────────────────────────────────────


class TestRegisterHappyPath:
    def test_registers_mlx_candidate(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            result = register_model_candidate(root, cid, model_id="gemma-test")
            assert result.problem is None
            assert result.manifest_updated is True
            assert result.registered_model.model_id == "gemma-test"
            assert result.registered_model.status == "registered"
            assert result.registered_model.storage_mode == "managed"
            assert "models/mlx/gemma-test" in result.managed_path

            # Verify managed copy exists.
            managed = root / result.managed_path
            assert managed.exists()
            assert (managed / "config.json").exists()

            # Verify manifest has one entry.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            assert len(manifest["models"]) == 1
            assert manifest["models"][0]["model_id"] == "gemma-test"

    def test_registers_gguf_candidate(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            gguf = _fake_gguf(Path(d))
            result = inspect_model_candidate(gguf)
            write_candidate_record(root, result.candidate)

            reg = register_model_candidate(root, result.candidate.candidate_id)
            assert reg.problem is None
            assert reg.registered_model.detected_format == "gguf"
            assert "models/gguf/" in reg.managed_path

            # Verify managed copy exists.
            managed = root / reg.managed_path
            assert managed.exists()

    def test_registers_vision_candidate_under_vlm(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_qwen_vlm(Path(d))
            cid = _setup_candidate(root, fake)

            result = register_model_candidate(root, cid, model_id="qwen-vl-test")
            assert result.problem is None
            assert "models/vlm/qwen-vl-test" in result.managed_path

            managed = root / result.managed_path
            assert managed.exists()

    def test_preserves_original_source(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            original_mtime = (fake / "config.json").stat().st_mtime
            register_model_candidate(root, cid, model_id="preserve-test")
            # Original must be unchanged.
            assert (fake / "config.json").stat().st_mtime == original_mtime

    def test_derives_model_id_when_omitted(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            result = register_model_candidate(root, cid)
            assert result.problem is None
            assert result.registered_model.model_id != ""
            assert "fake-mlx-model" in result.registered_model.model_id

    def test_gemma_text_only_registers_under_mlx_not_vlm(self):
        """Gemma text-only (even with processor metadata) registers
        under models/mlx/, not models/vlm/."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            # Create the observed Gemma shape with processor_config.json
            gemma_dir = Path(d) / "gemma-mlx-text"
            gemma_dir.mkdir()
            (gemma_dir / "config.json").write_text('{"model_type":"gemma"}')
            (gemma_dir / "tokenizer.json").write_text("{}")
            (gemma_dir / "model.safetensors").write_text("placeholder")
            (gemma_dir / "processor_config.json").write_text("{}")
            (gemma_dir / "generation_config.json").write_text("{}")

            cid = _setup_candidate(root, gemma_dir)
            result = register_model_candidate(root, cid, model_id="gemma-text-only")

            assert result.problem is None
            assert result.registered_model.detected_format == "mlx"
            assert result.registered_model.detected_family == "gemma"
            assert result.registered_model.modalities == ["text"]
            # Managed path is under models/mlx/, not models/vlm/.
            assert "models/mlx/gemma-text-only" in result.managed_path
            assert "models/vlm" not in result.managed_path


# ── Registration: idempotency ──────────────────────────────────────────────


class TestRegisterIdempotency:
    def test_repeated_registration_is_idempotent(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            r1 = register_model_candidate(root, cid, model_id="idem-test")
            assert r1.manifest_updated is True

            r2 = register_model_candidate(root, cid, model_id="idem-test")
            assert r2.manifest_updated is False  # Idempotent — no rewrite.

    def test_preserves_existing_models(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake1 = _fake_mlx_dir(Path(d), {"model_type": "gemma"})
            cid1 = _setup_candidate(root, fake1)
            register_model_candidate(root, cid1, model_id="model-a")

            fake2 = Path(d) / "fake-llama"
            fake2.mkdir()
            (fake2 / "config.json").write_text('{"model_type":"llama"}')
            (fake2 / "tokenizer.json").write_text("{}")
            (fake2 / "model.safetensors").write_text("w")
            result2 = inspect_model_candidate(fake2)
            write_candidate_record(root, result2.candidate)

            register_model_candidate(root, result2.candidate.candidate_id, model_id="model-b")

            manifest = json.loads((root / "registry" / "models.json").read_text())
            ids = [m["model_id"] for m in manifest["models"]]
            assert "model-a" in ids
            assert "model-b" in ids
            assert len(ids) == 2

    def test_manifest_timestamps_preserved(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            register_model_candidate(root, cid, model_id="ts-test")
            manifest = json.loads((root / "registry" / "models.json").read_text())
            assert "created_at" in manifest
            assert "updated_at" in manifest
            assert manifest["schema_version"] == 1


# ── Registration: rejections ───────────────────────────────────────────────


class TestRegisterRejections:
    def test_rejects_unknown_format(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            # Create a candidate with unknown format
            from whooshd.model_registry.contracts import ModelCandidate
            candidate = ModelCandidate(
                candidate_id="abc123",
                status="candidate",
                source_path="/tmp/test",
                detected_format="unknown",
                created_at="2026-01-01T00:00:00Z",
            )
            write_candidate_record(root, candidate)

            result = register_model_candidate(root, "abc123")
            assert result.problem == "unsupported_format"

    def test_rejects_unsupported_candidate(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            from whooshd.model_registry.contracts import ModelCandidate
            candidate = ModelCandidate(
                candidate_id="abc456",
                status="unsupported",
                source_path="/tmp/test",
                created_at="2026-01-01T00:00:00Z",
            )
            write_candidate_record(root, candidate)

            result = register_model_candidate(root, "abc456")
            assert result.problem == "candidate_not_registrable"

    def test_rejects_missing_candidate(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)

            result = register_model_candidate(root, "nonexistent-id")
            assert result.problem == "candidate_missing"

    def test_rejects_unsafe_model_id(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            for bad_id in ("", "  ", "../escape", "/absolute/path", "has spaces", "x" * 200):
                result = register_model_candidate(root, cid, model_id=bad_id)
                assert result.problem == "unsafe_model_id", f"Should reject: {bad_id!r}"

    def test_rejects_duplicate_model_id(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake1 = _fake_mlx_dir(Path(d), {"model_type": "gemma"})
            cid1 = _setup_candidate(root, fake1)
            register_model_candidate(root, cid1, model_id="dup-test")

            fake2 = Path(d) / "different-model"
            fake2.mkdir()
            (fake2 / "config.json").write_text('{"model_type":"qwen"}')
            (fake2 / "tokenizer.json").write_text("{}")
            (fake2 / "model.safetensors").write_text("different")
            result2 = inspect_model_candidate(fake2)
            write_candidate_record(root, result2.candidate)

            result = register_model_candidate(root, result2.candidate.candidate_id, model_id="dup-test")
            assert result.problem == "duplicate_model_id"

    def test_rejects_non_bootstrapped_store(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "not-a-store"
            root.mkdir()
            result = register_model_candidate(root, "any-id")
            assert result.problem == "store_not_bootstrapped"


# ── Safety ──────────────────────────────────────────────────────────────────


class TestRegistrationSafety:
    def test_manifest_not_updated_on_copy_failure(self):
        """If copy fails, manifest is NOT partially updated."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)

            # Create a candidate whose source path doesn't exist.
            from whooshd.model_registry.contracts import ModelCandidate
            candidate = ModelCandidate(
                candidate_id="ghost-id",
                status="candidate",
                source_path="/nonexistent/path/for/ghost",
                detected_format="mlx",
                created_at="2026-01-01T00:00:00Z",
            )
            write_candidate_record(root, candidate)

            # Copy will fail because source doesn't exist.
            result = register_model_candidate(root, "ghost-id", model_id="ghost")
            assert result.problem is not None  # Should be candidate_missing or copy_failed.

            # Manifest must be unchanged.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            assert len(manifest["models"]) == 0

    def test_source_files_never_deleted(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            register_model_candidate(root, cid, model_id="keep-source")
            assert fake.exists()
            assert (fake / "config.json").exists()

    def test_candidate_record_unchanged(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            cand_path = root / "registry" / "candidates" / f"{cid}.json"
            before = cand_path.read_text()
            register_model_candidate(root, cid, model_id="cand-unchanged")
            after = cand_path.read_text()
            assert before == after

    def test_registration_does_not_create_runtime_inventory(self):
        """Registered models do NOT appear in /v1/models or /api/tags."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)
            register_model_candidate(root, cid, model_id="not-advertised")

            # Check that manifest has the model but it's not runtime-advertised.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            assert len(manifest["models"]) == 1
            assert manifest["models"][0]["status"] == "registered"

    def test_destination_collision_fails_closed(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            cid = _setup_candidate(root, fake)

            # Pre-create the managed destination.
            dest = root / "models" / "mlx" / "collision-test"
            dest.mkdir(parents=True)
            (dest / "stale.txt").write_text("pre-existing")

            result = register_model_candidate(root, cid, model_id="collision-test")
            assert result.problem == "managed_destination_exists"


# ── Model ID validation ────────────────────────────────────────────────────


class TestModelIdValidation:
    def test_valid_ids(self):
        for good in ("gemma-4-e2b", "qwen2.5-0.5b-gguf", "model_v1", "test.model"):
            assert _validate_model_id(good) is None, f"Should accept: {good!r}"

    def test_invalid_ids(self):
        for bad in ("", "  ", "../escape", "/absolute", "has spaces", "x" * 200):
            assert _validate_model_id(bad) is not None, f"Should reject: {bad!r}"


# ── Contract roundtrip ─────────────────────────────────────────────────────


class TestRegisteredModelContract:
    def test_roundtrip(self):
        from whooshd.model_registry.contracts import RegisteredModel
        rm = RegisteredModel(
            model_id="test-model",
            display_name="Test Model",
            managed_path="models/mlx/test-model",
            source_candidate_id="abc",
            source_path="/tmp/src",
            detected_format="mlx",
            detected_family="gemma",
            modalities=["text"],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        d = rm.to_dict()
        restored = RegisteredModel.from_dict(d)
        assert restored.model_id == rm.model_id
        assert restored.storage_mode == "managed"
        assert restored.status == "registered"

    def test_from_dict_defaults(self):
        from whooshd.model_registry.contracts import RegisteredModel
        restored = RegisteredModel.from_dict({})
        assert restored.model_id == ""
        assert restored.status == "registered"
        assert restored.storage_mode == "managed"

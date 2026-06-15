"""Tests for registered model compatibility validation.

Validates adapter mapping, managed-file inspection, advertisability,
and read-only safety.  No real model files required.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import inspect_model_candidate, write_candidate_record
from whooshd.model_registry.compatibility import validate_registered_model_compatibility
from whooshd.model_registry.contracts import (
    RegisteredModelAdapterKind,
    RegisteredModelCompatibilityStatus,
)
from whooshd.model_registry.registration import register_model_candidate


# ── Helpers ─────────────────────────────────────────────────────────────────


def _setup_registered_model(root: Path, fake_dir: Path, model_id: str) -> None:
    """Bootstrap, inspect, register a fake MLX model."""
    bootstrap_model_store(root)
    result = inspect_model_candidate(fake_dir)
    write_candidate_record(root, result.candidate)
    register_model_candidate(root, result.candidate.candidate_id, model_id=model_id)


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


# ── Happy path: adapter mapping ────────────────────────────────────────────


class TestAdapterMapping:
    def test_mlx_text_maps_to_mlx_lm_server(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "gemma-test")

            result = validate_registered_model_compatibility(root, "gemma-test")
            assert result.adapter_kind == RegisteredModelAdapterKind.MLX_LM_SERVER
            assert result.status == RegisteredModelCompatibilityStatus.COMPATIBLE
            assert result.advertisable is True
            assert result.registered is True
            assert "found_config_json" in result.evidence
            assert "format_mlx" in result.evidence

    def test_gemma_text_only_maps_to_mlx_lm_server_not_vlm(self):
        """Gemma text-only (with processor metadata) maps to mlx_lm_server."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            gemma_dir = Path(d) / "gemma-mlx-text"
            gemma_dir.mkdir()
            (gemma_dir / "config.json").write_text('{"model_type":"gemma"}')
            (gemma_dir / "tokenizer.json").write_text("{}")
            (gemma_dir / "model.safetensors").write_text("placeholder")
            (gemma_dir / "processor_config.json").write_text("{}")
            (gemma_dir / "generation_config.json").write_text("{}")
            _setup_registered_model(root, gemma_dir, "gemma-text-only")

            result = validate_registered_model_compatibility(root, "gemma-text-only")
            assert result.adapter_kind == RegisteredModelAdapterKind.MLX_LM_SERVER
            assert "adapter_mlx_lm_server" in result.evidence
            # Must NOT claim vlm.
            assert result.adapter_kind != RegisteredModelAdapterKind.MLX_VLM
            assert "modalities_vision" not in result.evidence
            assert result.advertisable is True

    def test_mlx_vision_maps_to_mlx_vlm(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d), {
                "model_type": "qwen2_vl",
                "architectures": ["Qwen2VLForConditionalGeneration"],
            })
            _setup_registered_model(root, fake, "qwen-vl")

            result = validate_registered_model_compatibility(root, "qwen-vl")
            assert result.adapter_kind == RegisteredModelAdapterKind.MLX_VLM
            assert "modalities_vision" in result.evidence
            assert result.advertisable is True

    def test_gguf_maps_to_llama_cpp(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            gguf = _fake_gguf(Path(d))
            result = inspect_model_candidate(gguf)
            write_candidate_record(root, result.candidate)
            register_model_candidate(root, result.candidate.candidate_id, model_id="gguf-test")

            compat = validate_registered_model_compatibility(root, "gguf-test")
            assert compat.adapter_kind == RegisteredModelAdapterKind.LLAMA_CPP
            assert "format_gguf" in compat.evidence
            assert "found_gguf_file" in compat.evidence
            assert compat.advertisable is True


# ── Read-only safety ────────────────────────────────────────────────────────


class TestCompatibilityReadOnly:
    def test_does_not_mutate_manifest(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "ro-test")

            manifest_path = root / "registry" / "models.json"
            before = manifest_path.read_text()
            validate_registered_model_compatibility(root, "ro-test")
            after = manifest_path.read_text()
            assert before == after

    def test_does_not_mutate_managed_files(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "files-test")

            managed_config = root / "models" / "mlx" / "files-test" / "config.json"
            mtime_before = managed_config.stat().st_mtime
            validate_registered_model_compatibility(root, "files-test")
            mtime_after = managed_config.stat().st_mtime
            assert mtime_before == mtime_after

    def test_deterministic_except_checked_at(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "det-test")

            r1 = validate_registered_model_compatibility(root, "det-test")
            r2 = validate_registered_model_compatibility(root, "det-test")
            assert r1.status == r2.status
            assert r1.adapter_kind == r2.adapter_kind
            assert r1.advertisable == r2.advertisable


# ── Rejection: store/manifest problems ──────────────────────────────────────


class TestCompatibilityRejections:
    def test_non_bootstrapped_store(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "not-a-store"
            root.mkdir()
            result = validate_registered_model_compatibility(root, "any")
            assert "store_not_bootstrapped" in result.problems

    def test_missing_model(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            result = validate_registered_model_compatibility(root, "nonexistent")
            assert "model_missing" in result.problems

    def test_non_registered_status(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            # Manually insert a model with non-registered status.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "bad-status",
                "status": "invalid",
                "storage_mode": "managed",
                "managed_path": "models/mlx/bad",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "bad-status")
            assert "model_status_not_registered" in result.problems

    def test_unsupported_storage_mode(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "ext-ref",
                "status": "registered",
                "storage_mode": "external_reference",
                "managed_path": "models/mlx/ext",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "ext-ref")
            assert "unsupported_storage_mode" in result.problems

    def test_missing_managed_path(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "no-path",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "models/mlx/does-not-exist",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "no-path")
            assert "managed_path_missing" in result.problems

    def test_managed_path_escapes_store(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "escape",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "../../../etc/passwd",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "escape")
            assert "managed_path_escapes_store" in result.problems

    def test_unknown_format(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "models" / "mlx" / "unknown-fmt").mkdir(parents=True)
            (root / "models" / "mlx" / "unknown-fmt" / "config.json").write_text("{}")
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "unknown-fmt",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "models/mlx/unknown-fmt",
                "detected_format": "unknown",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "unknown-fmt")
            assert "unsupported_format" in result.problems

    def test_insufficient_evidence_not_advertisable(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "models" / "mlx" / "bare").mkdir(parents=True)
            # No config.json, no tokenizer — just a directory.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "bare",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "models/mlx/bare",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            result = validate_registered_model_compatibility(root, "bare")
            assert result.advertisable is False
            assert "insufficient_adapter_evidence" in result.problems

    def test_malformed_manifest(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "registry" / "models.json").write_text("{not json")

            result = validate_registered_model_compatibility(root, "any")
            assert "manifest_unreadable" in result.problems

    def test_unsupported_schema_version(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "registry" / "models.json").write_text(
                json.dumps({"schema_version": 99, "store_root": str(root), "models": []})
            )
            result = validate_registered_model_compatibility(root, "any")
            assert "manifest_schema_unsupported" in result.problems

    def test_missing_manifest(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            root.mkdir(parents=True)
            (root / "registry").mkdir()
            # No models.json
            result = validate_registered_model_compatibility(root, "any")
            assert "manifest_missing" in result.problems


# ── Contract roundtrip ─────────────────────────────────────────────────────


class TestCompatibilityContracts:
    def test_result_defaults(self):
        from whooshd.model_registry.contracts import RegisteredModelCompatibilityResult
        r = RegisteredModelCompatibilityResult(model_id="test")
        assert r.status == RegisteredModelCompatibilityStatus.INDETERMINATE
        assert r.adapter_kind == RegisteredModelAdapterKind.UNKNOWN
        assert r.advertisable is False
        assert r.registered is False

    def test_adapter_kind_values(self):
        assert RegisteredModelAdapterKind.MLX_LM_SERVER == "mlx_lm_server"
        assert RegisteredModelAdapterKind.MLX_VLM == "mlx_vlm"
        assert RegisteredModelAdapterKind.LLAMA_CPP == "llama_cpp"

    def test_compatibility_status_values(self):
        assert RegisteredModelCompatibilityStatus.COMPATIBLE == "compatible"
        assert RegisteredModelCompatibilityStatus.INCOMPATIBLE == "incompatible"
        assert RegisteredModelCompatibilityStatus.INDETERMINATE == "indeterminate"

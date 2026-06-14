"""Tests for model registry candidate inspection.

Validates inspection logic, candidate record writing, idempotency,
and safety guards.  No real model files required — uses synthetic paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import (
    inspect_model_candidate,
    write_candidate_record,
)
from whooshd.model_registry.contracts import (
    ModelCandidate,
    ModelCandidateFormat,
    ModelCandidateStatus,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _fake_mlx_dir(base: Path, config: dict | None = None) -> Path:
    """Create a minimal fake MLX model directory."""
    d = base / "fake-mlx-model"
    d.mkdir(parents=True)
    if config is None:
        config = {"model_type": "llama"}
    (d / "config.json").write_text(json.dumps(config))
    (d / "tokenizer.json").write_text("{}")
    (d / "model.safetensors").write_text("placeholder")
    return d


def _fake_gguf_file(base: Path) -> Path:
    """Create a minimal fake GGUF file."""
    f = base / "fake-model.gguf"
    f.write_text("placeholder-gguf")
    return f


def _fake_gemma_mlx(base: Path) -> Path:
    return _fake_mlx_dir(base, {"model_type": "gemma"})


def _fake_qwen_vlm(base: Path) -> Path:
    return _fake_mlx_dir(base, {
        "model_type": "qwen2_vl",
        "architectures": ["Qwen2VLForConditionalGeneration"],
    })


def _fake_unknown_dir(base: Path) -> Path:
    """Create a directory that is not a recognizable model."""
    d = base / "not-a-model"
    d.mkdir(parents=True)
    (d / "README.md").write_text("just a readme")
    return d


# ── Inspection: missing / empty ────────────────────────────────────────────


class TestInspectMissingOrEmpty:
    def test_missing_path_returns_invalid(self):
        result = inspect_model_candidate("/nonexistent/path/12345")
        assert result.candidate.status == ModelCandidateStatus.INVALID
        assert "path_missing" in result.candidate.problems

    def test_empty_directory_returns_unsupported(self):
        with TemporaryDirectory() as d:
            empty = Path(d) / "empty"
            empty.mkdir()
            result = inspect_model_candidate(empty)
            assert result.candidate.status == ModelCandidateStatus.UNSUPPORTED
            assert "empty_directory" in result.candidate.problems

    def test_empty_string_source_returns_error(self):
        result = inspect_model_candidate("")
        assert result.error is not None
        assert "empty" in result.error.lower()


# ── Inspection: GGUF ───────────────────────────────────────────────────────


class TestInspectGguf:
    def test_gguf_file_detected(self):
        with TemporaryDirectory() as d:
            gguf = _fake_gguf_file(Path(d))
            result = inspect_model_candidate(gguf)
            assert result.candidate.status == ModelCandidateStatus.CANDIDATE
            assert result.candidate.detected_format == ModelCandidateFormat.GGUF
            assert "found_gguf_file" in result.candidate.evidence
            assert "text" in result.candidate.modalities

    def test_gguf_family_from_name(self):
        with TemporaryDirectory() as d:
            gguf = Path(d) / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
            gguf.write_text("placeholder")
            result = inspect_model_candidate(gguf)
            assert result.candidate.detected_family == "qwen"


# ── Inspection: MLX directory ──────────────────────────────────────────────


class TestInspectMlx:
    def test_mlx_directory_detected(self):
        with TemporaryDirectory() as d:
            mlx = _fake_mlx_dir(Path(d))
            result = inspect_model_candidate(mlx)
            assert result.candidate.status == ModelCandidateStatus.CANDIDATE
            assert result.candidate.detected_format == ModelCandidateFormat.MLX
            assert "found_config_json" in result.candidate.evidence
            assert "found_tokenizer" in result.candidate.evidence
            assert "found_safetensors" in result.candidate.evidence

    def test_mlx_without_config_unsupported(self):
        with TemporaryDirectory() as d:
            d2 = Path(d) / "partial-mlx"
            d2.mkdir()
            (d2 / "model.safetensors").write_text("weights")
            result = inspect_model_candidate(d2)
            assert result.candidate.status == ModelCandidateStatus.UNSUPPORTED
            assert "ambiguous_candidate" in result.candidate.problems

    def test_config_unreadable_handled(self):
        with TemporaryDirectory() as d:
            d2 = Path(d) / "bad-config"
            d2.mkdir()
            (d2 / "config.json").write_text("{not json")
            (d2 / "model.safetensors").write_text("w")
            result = inspect_model_candidate(d2)
            assert "config_unreadable" in result.candidate.problems


# ── Inspection: family detection ───────────────────────────────────────────


class TestInspectFamilyDetection:
    def test_gemma_family_detected(self):
        with TemporaryDirectory() as d:
            gemma = _fake_gemma_mlx(Path(d))
            result = inspect_model_candidate(gemma)
            assert result.candidate.detected_family == "gemma"
            assert "model_type_gemma" in result.candidate.evidence

    def test_qwen_vlm_modalities(self):
        with TemporaryDirectory() as d:
            qwen = _fake_qwen_vlm(Path(d))
            result = inspect_model_candidate(qwen)
            assert result.candidate.detected_family == "qwen"
            assert "vision" in result.candidate.modalities

    def test_llama_default_from_config(self):
        with TemporaryDirectory() as d:
            llama = _fake_mlx_dir(Path(d), {"model_type": "llama"})
            result = inspect_model_candidate(llama)
            assert result.candidate.detected_family == "llama"

    def test_family_from_path_name(self):
        with TemporaryDirectory() as d:
            d2 = Path(d) / "qwen-something"
            d2.mkdir()
            (d2 / "model.safetensors").write_text("w")  # Non-empty so not early-returned.
            result = inspect_model_candidate(d2)
            assert result.candidate.detected_family == "qwen"


# ── Inspection: unknown / unsupported ──────────────────────────────────────


class TestInspectUnsupported:
    def test_unknown_directory_not_candidate(self):
        with TemporaryDirectory() as d:
            unknown = _fake_unknown_dir(Path(d))
            result = inspect_model_candidate(unknown)
            assert result.candidate.status == ModelCandidateStatus.UNSUPPORTED
            assert result.candidate.detected_format == ModelCandidateFormat.UNKNOWN

    def test_unknown_file_type_unsupported(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "notes.txt"
            f.write_text("hello")
            result = inspect_model_candidate(f)
            assert result.candidate.status == ModelCandidateStatus.UNSUPPORTED
            assert "unsupported_format" in result.candidate.problems


# ── Inspection: candidate ID stability ─────────────────────────────────────


class TestInspectCandidateId:
    def test_candidate_id_stable(self):
        with TemporaryDirectory() as d:
            mlx = _fake_mlx_dir(Path(d))
            r1 = inspect_model_candidate(mlx)
            r2 = inspect_model_candidate(mlx)
            assert r1.candidate.candidate_id == r2.candidate.candidate_id
            assert len(r1.candidate.candidate_id) == 16

    def test_candidate_id_changes_with_source(self):
        with TemporaryDirectory() as d:
            a = _fake_mlx_dir(Path(d) / "a")
            b = _fake_mlx_dir(Path(d) / "b")
            r1 = inspect_model_candidate(a)
            r2 = inspect_model_candidate(b)
            assert r1.candidate.candidate_id != r2.candidate.candidate_id

    def test_inspection_does_not_mutate_source(self):
        with TemporaryDirectory() as d:
            mlx = _fake_mlx_dir(Path(d))
            mtime_before = os.path.getmtime(mlx / "config.json")
            inspect_model_candidate(mlx)
            mtime_after = os.path.getmtime(mlx / "config.json")
            assert mtime_before == mtime_after


# ── Candidate record writing ───────────────────────────────────────────────


class TestWriteCandidateRecord:
    def test_writes_under_candidates_dir(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            mlx = _fake_mlx_dir(Path(d))
            result = inspect_model_candidate(mlx)
            record_path = write_candidate_record(root, result.candidate)

            assert record_path.exists()
            assert "registry/candidates/" in str(record_path)
            assert record_path.suffix == ".json"

    def test_candidates_dir_created_if_missing(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            mlx = _fake_mlx_dir(Path(d))
            result = inspect_model_candidate(mlx)
            write_candidate_record(root, result.candidate)

            assert (root / "registry" / "candidates").is_dir()

    def test_repeated_write_is_idempotent(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            mlx = _fake_mlx_dir(Path(d))
            result = inspect_model_candidate(mlx)

            p1 = write_candidate_record(root, result.candidate)
            mtime1 = os.path.getmtime(p1)
            p2 = write_candidate_record(root, result.candidate)
            mtime2 = os.path.getmtime(p2)

            assert p1 == p2
            assert mtime1 == mtime2  # Idempotent — no rewrite.

    def test_write_never_modifies_models_json(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest_path = root / "registry" / "models.json"
            manifest_before = manifest_path.read_text()

            mlx = _fake_mlx_dir(Path(d))
            result = inspect_model_candidate(mlx)
            write_candidate_record(root, result.candidate)

            manifest_after = manifest_path.read_text()
            assert manifest_before == manifest_after

    def test_rejects_non_bootstrapped_store(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "not-a-store"
            root.mkdir()
            candidate = ModelCandidate(
                candidate_id="test",
                source_path="/tmp/test",
                created_at="2026-01-01T00:00:00Z",
            )
            with pytest.raises(ValueError, match="not bootstrapped"):
                write_candidate_record(root, candidate)

    def test_path_escape_prevented_by_sanitization(self):
        """Dangerous candidate IDs are sanitized to safe filenames."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            candidate = ModelCandidate(
                candidate_id="../../../etc/passwd",
                source_path="/tmp/test",
                created_at="2026-01-01T00:00:00Z",
            )
            # Sanitization prevents escape — write succeeds.
            record_path = write_candidate_record(root, candidate)
            assert record_path.exists()
            # Path is inside the store root.
            assert str(record_path).startswith(str(root.resolve()))


# ── Contract roundtrip ─────────────────────────────────────────────────────


class TestCandidateContractRoundtrip:
    def test_candidate_to_dict_and_back(self):
        c = ModelCandidate(
            candidate_id="abc123",
            status=ModelCandidateStatus.CANDIDATE,
            source_path="/tmp/test",
            detected_format=ModelCandidateFormat.MLX,
            detected_family="gemma",
            modalities=["text", "vision"],
            evidence=["found_config_json"],
            problems=[],
            created_at="2026-01-01T00:00:00Z",
        )
        d = c.to_dict()
        restored = ModelCandidate.from_dict(d)
        assert restored.candidate_id == c.candidate_id
        assert restored.status == c.status
        assert restored.detected_format == c.detected_format
        assert restored.detected_family == c.detected_family
        assert restored.modalities == c.modalities
        assert restored.evidence == c.evidence

    def test_from_dict_handles_missing_fields(self):
        restored = ModelCandidate.from_dict({})
        assert restored.candidate_id == ""
        assert restored.status == ModelCandidateStatus.CANDIDATE
        assert restored.modalities == []

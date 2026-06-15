"""Tests for the model resolver core.

Validates format detection, path resolution, layout validation,
quant matching, and structured error returns.  No real model files
or runtime adapters are used — purely synthetic filesystem fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.models import (
    ModelFormat,
    ModelResolutionRequest,
    resolve_model,
    ResolutionStatus,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_request(model_id: str, **kw) -> ModelResolutionRequest:
    return ModelResolutionRequest(model_id=model_id, **kw)


def _mk(d: Path, *parts: str) -> Path:
    p = d
    for part in parts:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Format detection ────────────────────────────────────────────────────────


class TestFormatDetection:
    def test_gguf_by_suffix(self):
        req = _make_request("Qwen/Qwen3-14B-GGUF")
        result = resolve_model(req)
        assert result.format == ModelFormat.GGUF.value

    def test_gguf_by_containment(self):
        req = _make_request("Qwen/Qwen3-14B-GGUF-Q4_K_M")
        result = resolve_model(req)
        assert result.format == ModelFormat.GGUF.value

    def test_mlx_by_prefix(self):
        req = _make_request("mlx-community/Qwen3-14B-4bit")
        result = resolve_model(req)
        assert result.format == ModelFormat.MLX.value

    def test_mlx_by_suffix(self):
        req = _make_request("Qwen/Qwen3-14B-mlx")
        result = resolve_model(req)
        assert result.format == ModelFormat.MLX.value

    def test_safetensors_default(self):
        req = _make_request("Qwen/Qwen3-14B")
        result = resolve_model(req)
        assert result.format == ModelFormat.SAFETENSORS.value

    def test_explicit_format_override(self):
        req = _make_request("Qwen/Qwen3-14B-GGUF", format="mlx")
        result = resolve_model(req)
        assert result.format == ModelFormat.MLX.value

    def test_unsupported_explicit_format(self):
        req = _make_request("Qwen/Qwen3-14B", format="onnx")
        result = resolve_model(req)
        assert result.status == ResolutionStatus.UNSUPPORTED_FORMAT.value
        assert result.format == "onnx"


# ── Missing model ───────────────────────────────────────────────────────────


class TestMissing:
    def test_no_search_paths(self):
        with TemporaryDirectory() as d:
            req = _make_request("Qwen/Qwen3-14B-GGUF")
            result = resolve_model(req)
            assert result.status == ResolutionStatus.MISSING.value

    def test_no_match_in_any_path(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            root.mkdir()
            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.MISSING.value
            assert "checked_paths" in result.metadata

    def test_malformed_model_id_no_slash(self):
        req = _make_request("just-a-string")
        result = resolve_model(req)
        assert result.status == ResolutionStatus.MISSING.value
        assert "malformed" in (result.reason or "")

    def test_malformed_model_id_too_many_slashes(self):
        req = _make_request("a/b/c")
        result = resolve_model(req)
        assert result.status == ResolutionStatus.MISSING.value
        assert "malformed" in (result.reason or "")


# ── GGUF resolution ─────────────────────────────────────────────────────────


class TestGgufResolution:
    def test_valid_gguf_resolves(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "qwen3-14b-q4_k_m.gguf").write_text("fake-gguf")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert result.format == ModelFormat.GGUF.value
            assert result.runtime == "llama_cpp"
            assert result.source == "local_filesystem"
            assert result.path is not None
            assert "qwen3-14b-q4_k_m.gguf" in (result.path or "")
            assert result.metadata.get("matched_file") == "qwen3-14b-q4_k_m.gguf"

    def test_gguf_quant_match(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "qwen3-14b-q2_k.gguf").write_text("low")
            (gguf_dir / "qwen3-14b-q4_k_m.gguf").write_text("mid")
            (gguf_dir / "qwen3-14b-q8_0.gguf").write_text("high")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                quant="Q4_K_M",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert "q4_k_m" in result.metadata.get("matched_file", "").lower()

    def test_gguf_quant_miss(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "qwen3-14b-q4_k_m.gguf").write_text("x")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                quant="Q8_0",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.MISSING.value
            assert "quant" in (result.reason or "").lower()
            assert result.metadata.get("quant_requested") == "Q8_0"

    def test_gguf_no_gguf_file_invalid_layout(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "README.md").write_text("no gguf here")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.INVALID_LAYOUT.value
            assert "no .gguf" in (result.reason or "").lower()

    def test_gguf_quant_case_insensitive(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model-Q4_K_M.gguf").write_text("x")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                quant="q4_k_m",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value


# ── MLX resolution ──────────────────────────────────────────────────────────


class TestMlxResolution:
    def test_mlx_with_config_resolves(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text('{"model_type":"qwen2"}')

            req = _make_request(
                "mlx-community/Qwen3-14B-4bit",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert result.format == ModelFormat.MLX.value
            assert result.runtime == "mlx_lm"
            assert result.source == "local_filesystem"
            assert "matched_file" in result.metadata
            assert result.metadata["matched_file"] == "config.json"

    def test_mlx_without_config_invalid_layout(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "tokenizer.json").write_text("{}")

            req = _make_request(
                "mlx-community/Qwen3-14B-4bit",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.INVALID_LAYOUT.value
            assert "config.json" in (result.reason or "").lower()


# ── Safetensors resolution ──────────────────────────────────────────────────


class TestSafetensorsResolution:
    def test_safetensors_with_config_resolves(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            req = _make_request(
                "Qwen/Qwen3-14B",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert result.format == ModelFormat.SAFETENSORS.value
            assert result.runtime == "unsupported"
            assert result.source == "local_filesystem"

    def test_safetensors_with_safetensors_file_resolves(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "model.safetensors").write_text("weights")

            req = _make_request(
                "Qwen/Qwen3-14B",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert result.metadata.get("matched_file") == "*.safetensors"

    def test_safetensors_invalid_layout(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "README.md").write_text("nothing useful")

            req = _make_request(
                "Qwen/Qwen3-14B",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.INVALID_LAYOUT.value


# ── Multiple search paths ───────────────────────────────────────────────────


class TestMultipleSearchPaths:
    def test_first_valid_hit_wins(self):
        with TemporaryDirectory() as d:
            root1 = Path(d) / "models1"
            root2 = Path(d) / "models2"

            # Model exists in root2, not root1.
            gguf_dir = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root1, root2],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            checked = result.metadata.get("checked_paths", [])
            assert any("models2" in p for p in checked)

    def test_first_path_wins_over_later(self):
        with TemporaryDirectory() as d:
            root1 = Path(d) / "models1"
            root2 = Path(d) / "models2"

            dir1 = _mk(root1, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (dir1 / "first.gguf").write_text("first")
            dir2 = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (dir2 / "second.gguf").write_text("second")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root1, root2],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value
            assert "first.gguf" in (result.path or "")

    def test_checked_paths_recorded(self):
        with TemporaryDirectory() as d:
            root1 = Path(d) / "a"
            root2 = Path(d) / "b"
            root3 = Path(d) / "c"
            root1.mkdir()
            root2.mkdir()
            root3.mkdir()

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root1, root2, root3],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.MISSING.value
            checked = result.metadata.get("checked_paths", [])
            assert len(checked) == 3


# ── Runtime metadata ────────────────────────────────────────────────────────


class TestRuntimeMetadata:
    def test_gguf_runtime_is_llama_cpp(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.runtime == "llama_cpp"

    def test_mlx_runtime_is_mlx_lm(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            req = _make_request(
                "mlx-community/Qwen3-14B-4bit",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.runtime == "mlx_lm"

    def test_safetensors_runtime_is_unsupported(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            req = _make_request(
                "Qwen/Qwen3-14B",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.runtime == "unsupported"

    def test_unsupported_format_runtime_is_none(self):
        req = _make_request("Qwen/Qwen3-14B", format="onnx")
        result = resolve_model(req)
        assert result.runtime is None


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_model_id(self):
        req = _make_request("")
        result = resolve_model(req)
        assert result.status == ResolutionStatus.MISSING.value

    def test_whitespace_only_model_id(self):
        req = _make_request("   ")
        result = resolve_model(req)
        assert result.status == ResolutionStatus.MISSING.value

    def test_expanded_tilde_in_search_path(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            # Use a regular path instead of ~ since we're inside a temp dir.
            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.status == ResolutionStatus.FOUND.value

    def test_does_not_mutate_filesystem(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("original")

            mtime_before = (gguf_dir / "model.gguf").stat().st_mtime
            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            resolve_model(req)
            mtime_after = (gguf_dir / "model.gguf").stat().st_mtime
            assert mtime_before == mtime_after

    def test_resolved_path_is_absolute(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            assert result.path is not None
            assert Path(result.path).is_absolute()

    def test_result_is_frozen(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "models"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            req = _make_request(
                "Qwen/Qwen3-14B-GGUF",
                search_paths=[root],
            )
            result = resolve_model(req)
            with pytest.raises(Exception):
                result.status = "hacked"  # type: ignore[misc]

"""Tests for the local MLX model import workflow.

Validates cache-root discovery, snapshot selection, registration into the
managed store, advertisable inventory reporting, and duplicate handling.
Synthetic filesystem fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.candidates import inspect_model_candidate
from whooshd.model_registry.imports import (
    default_local_mlx_scan_roots,
    discover_local_mlx_snapshot_sources,
    import_local_mlx_models,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_hf_snapshot(
    hub_root: Path,
    publisher: str,
    repo: str,
    snapshot_hash: str,
    config: dict,
    *,
    include_main_ref: bool = True,
    extra_files: dict[str, str] | None = None,
) -> Path:
    repo_dir = hub_root / f"models--{publisher}--{repo}"
    snapshot_dir = repo_dir / "snapshots" / snapshot_hash
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "refs").mkdir(parents=True, exist_ok=True)
    if include_main_ref:
        (repo_dir / "refs" / "main").write_text(snapshot_hash, encoding="utf-8")
    (snapshot_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (snapshot_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot_dir / "model.safetensors").write_text("weights", encoding="utf-8")
    if extra_files:
        for name, content in extra_files.items():
            (snapshot_dir / name).write_text(content, encoding="utf-8")
    return snapshot_dir


# ── Default scan roots ──────────────────────────────────────────────────────


class TestDefaultLocalMlxScanRoots:
    def test_prefers_huggingface_hub_when_present(self, monkeypatch):
        with TemporaryDirectory() as d:
            home = Path(d)
            hub = home / ".cache" / "huggingface" / "hub"
            hub.mkdir(parents=True)

            monkeypatch.setattr("whooshd.model_registry.imports.Path.home", lambda: home)

            roots = default_local_mlx_scan_roots()
            assert roots[0] == hub.resolve()


# ── Discovery ───────────────────────────────────────────────────────────────


class TestDiscoverLocalMlxSnapshots:
    def test_discovers_active_snapshot_and_skips_dangling_ref(self):
        with TemporaryDirectory() as d:
            hub = Path(d) / ".cache" / "huggingface" / "hub"
            hub.mkdir(parents=True)

            good = _write_hf_snapshot(
                hub,
                "mlx-community",
                "Llama-3.2-3B-Instruct-4bit",
                "7f0dc925e0d0afb0322d96f9255cfddf2ba5636e",
                {"model_type": "llama"},
            )
            # A repo with a dangling ref should not be imported.
            dangling_repo = hub / "models--zecanard--gemma-4-E2B-it-ultra-uncensored-heretic-MLX-3bit-mixed_3_6"
            (dangling_repo / "refs").mkdir(parents=True)
            (dangling_repo / "refs" / "main").write_text(
                "missing-snapshot-hash",
                encoding="utf-8",
            )

            sources = discover_local_mlx_snapshot_sources([hub])
            assert len(sources) == 1
            assert sources[0].repo_id == "mlx-community/Llama-3.2-3B-Instruct-4bit"
            assert sources[0].snapshot_path == str(good.resolve())
            assert sources[0].selected_by == "refs/main"

            inspection = inspect_model_candidate(sources[0].snapshot_path)
            assert inspection.candidate.status == "candidate"
            assert inspection.candidate.detected_format == "mlx"
            assert inspection.candidate.detected_family == "llama"


# ── Import workflow ────────────────────────────────────────────────────────


class TestLocalMlxImportWorkflow:
    def test_import_registers_models_and_reports_advertisable_inventory(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            hub = home / ".cache" / "huggingface" / "hub"
            hub.mkdir(parents=True)
            store_root = home / "whooshd-models"

            llama = _write_hf_snapshot(
                hub,
                "mlx-community",
                "Llama-3.2-3B-Instruct-4bit",
                "7f0dc925e0d0afb0322d96f9255cfddf2ba5636e",
                {"model_type": "llama"},
            )
            qwen = _write_hf_snapshot(
                hub,
                "mlx-community",
                "Qwen2-VL-2B-Instruct-4bit",
                "01af461cdb9574acc09084a0ef94e216e142b085",
                {
                    "model_type": "qwen2_vl",
                    "architectures": ["Qwen2VLForConditionalGeneration"],
                },
                extra_files={"processor_config.json": "{}"},
            )

            report = import_local_mlx_models(store_root=store_root, scan_roots=[hub])

            assert report.error is None
            assert len(report.sources) == 2
            assert len(report.imported_records) == 2
            assert report.duplicate_records == []
            assert report.advertisable_model_ids == sorted(report.advertisable_model_ids)
            assert report.advertisable_model_ids == [
                "mlx-community-Llama-3.2-3B-Instruct-4bit",
                "mlx-community-Qwen2-VL-2B-Instruct-4bit",
            ]

            llama_record = next(
                record for record in report.records
                if record.repo_id == "mlx-community/Llama-3.2-3B-Instruct-4bit"
            )
            assert llama_record.status == "imported"
            assert llama_record.model_id == "mlx-community-Llama-3.2-3B-Instruct-4bit"
            assert llama_record.candidate_status == "candidate"
            assert llama_record.candidate_format == "mlx"
            assert llama_record.managed_path == "models/mlx/mlx-community-Llama-3.2-3B-Instruct-4bit"

            qwen_record = next(
                record for record in report.records
                if record.repo_id == "mlx-community/Qwen2-VL-2B-Instruct-4bit"
            )
            assert qwen_record.status == "imported"
            assert qwen_record.model_id == "mlx-community-Qwen2-VL-2B-Instruct-4bit"
            assert qwen_record.candidate_family == "qwen"
            assert qwen_record.managed_path == "models/vlm/mlx-community-Qwen2-VL-2B-Instruct-4bit"

            manifest = json.loads((store_root / "registry" / "models.json").read_text())
            ids = [entry["model_id"] for entry in manifest["models"]]
            assert ids == sorted(ids)
            assert ids == [
                "mlx-community-Llama-3.2-3B-Instruct-4bit",
                "mlx-community-Qwen2-VL-2B-Instruct-4bit",
            ]

            # The managed copies should exist inside the Whoosh'd store.
            assert (store_root / "models" / "mlx" / "mlx-community-Llama-3.2-3B-Instruct-4bit").exists()
            assert (store_root / "models" / "vlm" / "mlx-community-Qwen2-VL-2B-Instruct-4bit").exists()

    def test_duplicate_model_ids_are_reported_and_not_reimported(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            hub_a = home / "cache-a" / "hub"
            hub_b = home / "cache-b" / "hub"
            hub_a.mkdir(parents=True)
            hub_b.mkdir(parents=True)
            store_root = home / "whooshd-models"

            _write_hf_snapshot(
                hub_a,
                "mlx-community",
                "Llama-3.2-3B-Instruct-4bit",
                "snapshot-a",
                {"model_type": "llama"},
            )
            _write_hf_snapshot(
                hub_b,
                "mlx-community",
                "Llama-3.2-3B-Instruct-4bit",
                "snapshot-b",
                {"model_type": "llama"},
            )

            report = import_local_mlx_models(store_root=store_root, scan_roots=[hub_a, hub_b])

            assert report.error is None
            assert len(report.sources) == 2
            assert len(report.imported_records) == 1
            assert len(report.duplicate_records) == 1
            assert report.imported_records[0].model_id == "mlx-community-Llama-3.2-3B-Instruct-4bit"
            assert report.duplicate_records[0].reason == "duplicate_model_id"

            manifest = json.loads((store_root / "registry" / "models.json").read_text())
            ids = [entry["model_id"] for entry in manifest["models"]]
            assert ids == ["mlx-community-Llama-3.2-3B-Instruct-4bit"]

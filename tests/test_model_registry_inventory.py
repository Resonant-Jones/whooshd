"""Tests for registered model inventory advertisement.

Validates that compatible registered models appear in /v1/models
and /api/tags, incompatible models are skipped, and existing
built-in/static models remain intact.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import inspect_model_candidate, write_candidate_record
from whooshd.model_registry.inventory import collect_advertisable_registered_models
from whooshd.model_registry.registration import register_model_candidate


# ── Helpers ─────────────────────────────────────────────────────────────────


def _setup_registered_model(root: Path, fake_dir: Path, model_id: str,
                            display_name: str = "") -> None:
    bootstrap_model_store(root)
    result = inspect_model_candidate(fake_dir)
    write_candidate_record(root, result.candidate)
    register_model_candidate(root, result.candidate.candidate_id,
                             model_id=model_id, display_name=display_name)


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


# ── Inventory collection ───────────────────────────────────────────────────


class TestCollectAdvertisableModels:
    def test_collects_compatible_mlx(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "gemma-mlx", "Gemma MLX")

            models = collect_advertisable_registered_models(root)
            assert len(models) == 1
            assert models[0].model_id == "gemma-mlx"

    def test_collects_compatible_gguf(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            gguf = _fake_gguf(Path(d))
            result = inspect_model_candidate(gguf)
            write_candidate_record(root, result.candidate)
            register_model_candidate(root, result.candidate.candidate_id, model_id="gguf-model")

            models = collect_advertisable_registered_models(root)
            assert len(models) == 1
            assert models[0].model_id == "gguf-model"

    def test_collects_compatible_vlm(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d), {
                "model_type": "qwen2_vl",
                "architectures": ["Qwen2VLForConditionalGeneration"],
            })
            _setup_registered_model(root, fake, "qwen-vlm")

            models = collect_advertisable_registered_models(root)
            assert len(models) == 1
            assert models[0].model_id == "qwen-vlm"

    def test_skips_incompatible(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            # Insert a manifest entry with no managed path.
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "no-path",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "models/mlx/does-not-exist",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            models = collect_advertisable_registered_models(root)
            assert len(models) == 0

    def test_skips_non_registered_status(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "bad-status",
                "status": "invalid",
                "storage_mode": "managed",
                "managed_path": "models/mlx/bad",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            models = collect_advertisable_registered_models(root)
            assert len(models) == 0

    def test_read_only(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "ro-test")

            manifest_path = root / "registry" / "models.json"
            before = manifest_path.read_text()
            collect_advertisable_registered_models(root)
            after = manifest_path.read_text()
            assert before == after

    def test_empty_on_missing_store(self):
        models = collect_advertisable_registered_models("/nonexistent/store/path")
        assert models == []

    def test_empty_on_malformed_manifest(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "registry" / "models.json").write_text("{not json")
            models = collect_advertisable_registered_models(root)
            assert models == []

    def test_empty_on_unsupported_schema(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            (root / "registry" / "models.json").write_text(
                json.dumps({"schema_version": 99, "store_root": str(root), "models": []})
            )
            models = collect_advertisable_registered_models(root)
            assert models == []

    def test_stable_ordering(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "b-model")
            fake2 = Path(d) / "fake-model-2"
            fake2.mkdir()
            (fake2 / "config.json").write_text('{"model_type":"gemma"}')
            (fake2 / "tokenizer.json").write_text("{}")
            (fake2 / "model.safetensors").write_text("w")
            result2 = inspect_model_candidate(fake2)
            write_candidate_record(root, result2.candidate)
            register_model_candidate(root, result2.candidate.candidate_id, model_id="a-model")

            models = collect_advertisable_registered_models(root)
            ids = [m.model_id for m in models]
            assert ids == sorted(ids)
            assert ids[0] == "a-model"
            assert ids[1] == "b-model"


# ── HTTP route integration ─────────────────────────────────────────────────


class TestV1ModelsWithRegistered:
    @pytest.mark.asyncio
    async def test_registered_model_appears_in_v1_models(self, monkeypatch):
        """When model-store is configured, registered models appear in /v1/models."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "gemma-mlx", "Gemma MLX")

            monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", str(root))

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                assert resp.status_code == 200
                data = resp.json()["data"]
                ids = [m["id"] for m in data]
                assert "gemma-mlx" in ids
                # Find the registered entry.
                gemma = next(m for m in data if m["id"] == "gemma-mlx")
                assert gemma["metadata"]["source"] == "registered"
                assert gemma["metadata"]["format"] == "mlx"
                assert gemma["metadata"]["engine"] == "mlx_lm_server"

    @pytest.mark.asyncio
    async def test_registered_model_appears_in_api_tags(self, monkeypatch):
        """Registered models appear in /api/tags."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "gemma-mlx")

            monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", str(root))

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/tags")
                assert resp.status_code == 200
                models = resp.json()["models"]
                names = [m["name"] for m in models]
                assert "gemma-mlx" in names

    @pytest.mark.asyncio
    async def test_incompatible_model_not_in_v1_models(self, monkeypatch):
        """Incompatible registered models do NOT appear."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            bootstrap_model_store(root)
            manifest = json.loads((root / "registry" / "models.json").read_text())
            manifest["models"].append({
                "model_id": "no-path",
                "status": "registered",
                "storage_mode": "managed",
                "managed_path": "models/mlx/ghost",
                "detected_format": "mlx",
            })
            (root / "registry" / "models.json").write_text(json.dumps(manifest))

            monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", str(root))

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                ids = [m["id"] for m in resp.json()["data"]]
                assert "no-path" not in ids

    @pytest.mark.asyncio
    async def test_existing_models_still_appear(self, monkeypatch):
        """Built-in/static models remain visible when registry is active."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "gemma-mlx")

            monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", str(root))

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                ids = [m["id"] for m in resp.json()["data"]]
                # Built-in stub-model still appears.
                assert "stub-model" in ids
                assert "gemma-mlx" in ids

    @pytest.mark.asyncio
    async def test_missing_store_does_not_break_inventory(self, monkeypatch):
        """Missing model-store root does not break existing inventory."""
        monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", "/nonexistent/path/99999")

        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "stub-model" in ids

    @pytest.mark.asyncio
    async def test_registered_model_does_not_duplicate_builtin_id(self, monkeypatch):
        """If a registered model ID duplicates a built-in, it's skipped."""
        with TemporaryDirectory() as d:
            root = Path(d) / "store"
            fake = _fake_mlx_dir(Path(d))
            _setup_registered_model(root, fake, "stub-model")  # Same as built-in

            monkeypatch.setenv("WHOOSHD_MODEL_STORE_ROOT", str(root))

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                data = resp.json()["data"]
                ids = [m["id"] for m in data]
                # stub-model appears exactly once.
                assert ids.count("stub-model") == 1

"""Tests for external model inventory scanning and API exposure.

Validates inventory discovery from external route roots, quant extraction,
model ID rules, duplicate handling, hidden directory skipping, and
/v1/models + /api/tags integration.  Synthetic filesystem fixtures only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.models.inventory import (
    _extract_quant,
    list_external_model_inventory,
)
from whooshd.models.routes import ExternalWeightRoute
from whooshd.models.types import (
    ExternalModelInventoryEntry,
    ModelFormat,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _route(id: str, path: Path, **kw) -> ExternalWeightRoute:
    return ExternalWeightRoute(id=id, path=path, **kw)


def _mk(d: Path, *parts: str) -> Path:
    p = d
    for part in parts:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Quant extraction ────────────────────────────────────────────────────────


class TestQuantExtraction:
    def test_q4_k_m(self):
        assert _extract_quant("qwen3-14b-Q4_K_M") == "Q4_K_M"

    def test_q5_k_m(self):
        assert _extract_quant("model-Q5_K_M-gguf") == "Q5_K_M"

    def test_q8_0(self):
        assert _extract_quant("something_Q8_0") == "Q8_0"

    def test_case_insensitive(self):
        assert _extract_quant("model-q4_k_m") == "Q4_K_M"

    def test_regex_fallback(self):
        # Q2_K not in known set, but matches regex.
        assert _extract_quant("model-Q2_K") == "Q2_K"

    def test_no_quant_returns_none(self):
        assert _extract_quant("model-weights") is None

    def test_q4_0_fallback(self):
        assert _extract_quant("model-Q4_0") == "Q4_0"


# ── Inventory: basic discovery ──────────────────────────────────────────────


class TestBasicDiscovery:
    def test_empty_routes_returns_empty(self):
        result = list_external_model_inventory([])
        assert result == []

    def test_disabled_route_ignored(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            routes = [_route("off", root, enabled=False)]
            result = list_external_model_inventory(routes)
            assert result == []

    def test_unavailable_route_ignored(self):
        routes = [_route("ghost", Path("/Volumes/Ghost/models"))]
        result = list_external_model_inventory(routes)
        assert result == []


# ── Inventory: GGUF ─────────────────────────────────────────────────────────


class TestGgufInventory:
    def test_gguf_discovered(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 1
            assert result[0].id == "Qwen/Qwen3-14B-GGUF:Q4_K_M"
            assert result[0].model_id == "Qwen/Qwen3-14B-GGUF"
            assert result[0].format == ModelFormat.GGUF.value
            assert result[0].runtime == "llama_cpp"
            assert result[0].source == "external"
            assert result[0].registry_managed is False
            assert result[0].servable is True
            assert result[0].route_id == "vault"
            assert result[0].metadata.get("quant") == "Q4_K_M"

    def test_gguf_multiple_quants(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("q4")
            (gguf_dir / "Qwen3-14B-Q8_0.gguf").write_text("q8")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 2
            ids = [e.id for e in result]
            assert "Qwen/Qwen3-14B-GGUF:Q4_K_M" in ids
            assert "Qwen/Qwen3-14B-GGUF:Q8_0" in ids

    def test_gguf_no_quant_uses_file_stem(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "SomeModel")
            (gguf_dir / "model-weights.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 1
            # Should be deterministic — uses file stem.
            assert result[0].id.startswith("Qwen/SomeModel:")
            assert "model-weights" in result[0].id

    def test_gguf_hidden_files_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / ".hidden.gguf").write_text("hidden")
            (gguf_dir / "visible.gguf").write_text("visible")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            # Only visible file counts (no recognized quant, uses stem).
            ids = [e.id for e in result]
            assert all(not i.startswith(".") for i in ids)
            assert any("visible" in i for i in ids)

    def test_gguf_empty_dir_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            _mk(root, "gguf", "Qwen", "EmptyDir")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []


# ── Inventory: MLX ──────────────────────────────────────────────────────────


class TestMlxInventory:
    def test_mlx_with_config_discovered(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 1
            assert result[0].id == "mlx-community/Qwen3-14B-4bit"
            assert result[0].format == ModelFormat.MLX.value
            assert result[0].runtime == "mlx_lm"
            assert result[0].servable is True

    def test_mlx_without_config_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "BadModel")
            (mlx_dir / "tokenizer.json").write_text("{}")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []


# ── Inventory: safetensors ──────────────────────────────────────────────────


class TestSafetensorsInventory:
    def test_safetensors_with_config_discovered(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 1
            assert result[0].id == "Qwen/Qwen3-14B"
            assert result[0].format == ModelFormat.SAFETENSORS.value
            assert result[0].runtime == "unsupported"
            assert result[0].servable is False

    def test_safetensors_with_safetensors_file_discovered(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "model.safetensors").write_text("weights")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert len(result) == 1
            assert result[0].id == "Qwen/Qwen3-14B"

    def test_safetensors_empty_dir_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            _mk(root, "safetensors", "Qwen", "Empty")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []


# ── Hidden directories ──────────────────────────────────────────────────────


class TestHiddenDirs:
    def test_dot_cache_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            # .cache as publisher should be skipped.
            cache_dir = _mk(root, "gguf", ".cache", "SomeModel")
            (cache_dir / "model.gguf").write_text("x")
            # Normal publisher should still work.
            normal_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (normal_dir / "model.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            # Only the normal one — .cache skipped.
            ids = [e.id for e in result]
            assert len(ids) == 1
            assert "Qwen" in ids[0]

    def test_huggingface_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            cache_dir = _mk(root, "gguf", ".huggingface", "x")
            (cache_dir / "model.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []

    def test_git_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            cache_dir = _mk(root, "gguf", ".git", "x")
            (cache_dir / "model.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []

    def test_ds_store_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            cache_dir = _mk(root, "gguf", ".DS_Store", "x")
            (cache_dir / "model.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []

    def test_pycache_skipped(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            cache_dir = _mk(root, "gguf", "__pycache__", "x")
            (cache_dir / "model.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert result == []


# ── Route priority ──────────────────────────────────────────────────────────


class TestRoutePriority:
    def test_available_route_honored(self):
        with TemporaryDirectory() as d:
            root1 = (Path(d) / "primary").resolve(); root1.mkdir()
            root2 = (Path(d) / "secondary").resolve(); root2.mkdir()

            # Only secondary has the model.
            gguf_dir = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")

            routes = [
                _route("primary", root1, priority=10),
                _route("secondary", root2, priority=20),
            ]
            result = list_external_model_inventory(routes)
            assert len(result) == 1
            assert result[0].route_id == "secondary"

    def test_disabled_route_ignored_even_with_low_priority(self):
        with TemporaryDirectory() as d:
            root1 = (Path(d) / "a").resolve(); root1.mkdir()
            root2 = (Path(d) / "b").resolve(); root2.mkdir()

            gguf_dir = _mk(root1, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("x")
            _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (root2 / "gguf" / "Qwen" / "Qwen3-14B-GGUF" / "model.gguf").write_text("x")

            routes = [
                _route("off", root1, enabled=False, priority=1),
                _route("on", root2, priority=100),
            ]
            result = list_external_model_inventory(routes)
            assert len(result) == 1
            assert result[0].route_id == "on"


# ── Duplicate handling ──────────────────────────────────────────────────────


class TestDuplicates:
    def test_same_model_multiple_routes_prefers_first(self):
        """When the same model appears in multiple routes, the first
        (higher priority) route wins.  The duplicate is skipped."""
        with TemporaryDirectory() as d:
            root1 = (Path(d) / "primary").resolve(); root1.mkdir()
            root2 = (Path(d) / "secondary").resolve(); root2.mkdir()

            d1 = _mk(root1, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d1 / "model.gguf").write_text("first")
            d2 = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d2 / "model.gguf").write_text("second")

            routes = [
                _route("primary", root1, priority=10),
                _route("secondary", root2, priority=20),
            ]
            result = list_external_model_inventory(routes)
            assert len(result) == 1
            assert result[0].route_id == "primary"

    def test_different_models_same_publisher_different_repo(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            d1 = _mk(root, "gguf", "Qwen", "ModelA")
            (d1 / "model.gguf").write_text("a")
            d2 = _mk(root, "gguf", "Qwen", "ModelB")
            (d2 / "model.gguf").write_text("b")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)
            assert len(result) == 2
            ids = [e.id for e in result]
            assert any("ModelA" in i for i in ids)
            assert any("ModelB" in i for i in ids)


# ── Inventory entry fields ──────────────────────────────────────────────────


class TestEntryFields:
    def test_all_fields_present(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("x")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            e = result[0]
            assert e.id
            assert e.model_id
            assert e.source == "external"
            assert e.route_id == "vault"
            assert e.format == "gguf"
            assert e.runtime == "llama_cpp"
            assert e.path
            assert e.registry_managed is False
            assert e.path_available is True
            assert e.servable is True
            assert isinstance(e.metadata, dict)

    def test_safetensors_entry_not_servable(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = list_external_model_inventory(routes)

            assert result[0].servable is False
            assert result[0].runtime == "unsupported"


# ── Read-only constraint ────────────────────────────────────────────────────


class TestReadOnly:
    def test_scanner_does_not_create_directories(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()

            routes = [_route("vault", root)]
            list_external_model_inventory(routes)

            # No new directories created.
            children = list(root.iterdir())
            assert children == []

    def test_scanner_does_not_mutate_files(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            gguf_file = gguf_dir / "model.gguf"
            gguf_file.write_text("original")

            mtime_before = gguf_file.stat().st_mtime
            routes = [_route("vault", root)]
            list_external_model_inventory(routes)
            mtime_after = gguf_file.stat().st_mtime
            assert mtime_before == mtime_after


# ── API integration: /v1/models ─────────────────────────────────────────────


class TestV1ModelsIntegration:
    @pytest.mark.asyncio
    async def test_external_models_in_v1_models(self, monkeypatch):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                assert resp.status_code == 200
                data = resp.json()["data"]
                ids = [m["id"] for m in data]
                assert "Qwen/Qwen3-14B-GGUF:Q4_K_M" in ids

                # Check metadata.
                ext = next(m for m in data if m["id"] == "Qwen/Qwen3-14B-GGUF:Q4_K_M")
                assert ext["metadata"]["source"] == "external"
                assert ext["metadata"]["registry_managed"] is False
                assert ext["metadata"]["route_id"] == "vault"
                assert ext["metadata"]["format"] == "gguf"

    @pytest.mark.asyncio
    async def test_existing_models_still_in_v1_models(self, monkeypatch):
        """Built-in models remain when external routes are configured."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")
                assert resp.status_code == 200
                ids = [m["id"] for m in resp.json()["data"]]
                assert "stub-model" in ids

    @pytest.mark.asyncio
    async def test_no_external_routes_still_works(self, monkeypatch):
        """When no external routes are configured, everything works normally."""
        monkeypatch.delenv("WHOOSHD_EXTERNAL_ROUTES", raising=False)

        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "stub-model" in ids


# ── API integration: /api/tags ──────────────────────────────────────────────


class TestApiTagsIntegration:
    @pytest.mark.asyncio
    async def test_external_models_in_api_tags(self, monkeypatch):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/tags")
                assert resp.status_code == 200
                models = resp.json()["models"]
                names = [m["name"] for m in models]
                assert "Qwen/Qwen3-14B-GGUF:Q4_K_M" in names

                ext = next(m for m in models if m["name"] == "Qwen/Qwen3-14B-GGUF:Q4_K_M")
                assert ext["details"]["source"] == "external"

    @pytest.mark.asyncio
    async def test_existing_tags_still_in_api_tags(self, monkeypatch):
        """Built-in tags remain when external routes are configured."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from httpx import ASGITransport, AsyncClient
            from whooshd.app import app

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/tags")
                assert resp.status_code == 200
                names = [m["name"] for m in resp.json()["models"]]
                assert "stub-model" in names


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_route_with_no_format_dirs(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()  # No gguf/mlx/safetensors subdirs.

            routes = [_route("empty", root)]
            result = list_external_model_inventory(routes)
            assert result == []

    def test_multiple_routes_yield_unique_entries(self):
        with TemporaryDirectory() as d:
            root1 = (Path(d) / "r1").resolve(); root1.mkdir()
            root2 = (Path(d) / "r2").resolve(); root2.mkdir()

            d1 = _mk(root1, "gguf", "A", "Model1")
            (d1 / "m.gguf").write_text("1")
            d2 = _mk(root2, "gguf", "B", "Model2")
            (d2 / "m.gguf").write_text("2")

            routes = [
                _route("r1", root1, priority=10),
                _route("r2", root2, priority=20),
            ]
            result = list_external_model_inventory(routes)
            ids = [e.id for e in result]
            assert len(set(ids)) == len(ids)
            assert len(ids) == 2

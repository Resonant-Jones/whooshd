"""Tests for external weight routes.

Validates route loading, availability detection, mount detection,
priority ordering, resolver composition, and metadata enrichment.
Synthetic filesystem fixtures only — no real external drives.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.models.routes import (
    ExternalRouteStatus,
    ExternalWeightRoute,
    get_available_route_paths,
    load_external_weight_routes,
    resolve_model_from_routes,
    validate_all_routes,
    validate_route_status,
)
from whooshd.models.types import (
    ModelResolutionRequest,
    ResolutionStatus,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mk(d: Path, *parts: str) -> Path:
    p = d
    for part in parts:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


def _route(id: str, path: Path, **kw) -> ExternalWeightRoute:
    return ExternalWeightRoute(id=id, path=path, **kw)


# ── Route loading ───────────────────────────────────────────────────────────


class TestLoadRoutes:
    def test_empty_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_EXTERNAL_ROUTES", raising=False)
        routes = load_external_weight_routes()
        assert routes == []

    def test_blank_env_returns_empty(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_EXTERNAL_ROUTES", "   ")
        routes = load_external_weight_routes()
        assert routes == []

    def test_invalid_json_returns_empty(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_EXTERNAL_ROUTES", "{not json")
        routes = load_external_weight_routes()
        assert routes == []

    def test_non_list_json_returns_empty(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_EXTERNAL_ROUTES", '{"id": "x"}')
        routes = load_external_weight_routes()
        assert routes == []

    def test_loads_single_route(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"id": "vaultnode", "path": "/Volumes/VaultNode/models"}
            ]),
        )
        routes = load_external_weight_routes()
        assert len(routes) == 1
        assert routes[0].id == "vaultnode"
        assert str(routes[0].path) == "/Volumes/VaultNode/models"
        assert routes[0].enabled is True
        assert routes[0].read_only is True
        assert routes[0].priority == 100

    def test_loads_multiple_routes(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"id": "a", "path": "/tmp/a"},
                {"id": "b", "path": "/tmp/b", "priority": 50},
            ]),
        )
        routes = load_external_weight_routes()
        assert len(routes) == 2
        assert routes[1].priority == 50

    def test_skips_missing_id(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"path": "/tmp/x"},
            ]),
        )
        routes = load_external_weight_routes()
        assert routes == []

    def test_skips_missing_path(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"id": "x"},
            ]),
        )
        routes = load_external_weight_routes()
        assert routes == []

    def test_skips_non_dict_entries(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                "just a string",
                {"id": "real", "path": "/tmp/real"},
            ]),
        )
        routes = load_external_weight_routes()
        assert len(routes) == 1
        assert routes[0].id == "real"

    def test_disabled_flag_parsed(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"id": "off", "path": "/tmp/off", "enabled": False},
            ]),
        )
        routes = load_external_weight_routes()
        assert routes[0].enabled is False

    def test_priority_parsed(self, monkeypatch):
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([
                {"id": "p", "path": "/tmp/p", "priority": 10},
            ]),
        )
        routes = load_external_weight_routes()
        assert routes[0].priority == 10


# ── Route validation: available ────────────────────────────────────────────


class TestAvailable:
    def test_enabled_available_route(self):
        with TemporaryDirectory() as d:
            route = _route("vault", Path(d))
            status = validate_route_status(route)
            assert status.status == ExternalRouteStatus.AVAILABLE.value
            assert status.available is True
            assert status.id == "vault"

    def test_multiple_routes_all_available(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir()
            b.mkdir()
            routes = [_route("a", a), _route("b", b)]
            statuses = validate_all_routes(routes)
            assert all(s.available for s in statuses)


# ── Route validation: disabled ─────────────────────────────────────────────


class TestDisabled:
    def test_disabled_route(self):
        with TemporaryDirectory() as d:
            route = _route("off", Path(d), enabled=False)
            status = validate_route_status(route)
            assert status.status == ExternalRouteStatus.DISABLED.value
            assert status.available is False
            assert "disabled" in (status.reason or "")

    def test_disabled_even_when_path_exists(self):
        with TemporaryDirectory() as d:
            route = _route("off", Path(d), enabled=False)
            status = validate_route_status(route)
            assert status.available is False


# ── Route validation: mount_unavailable ─────────────────────────────────────


class TestMountUnavailable:
    def test_mount_point_missing(self):
        route = _route("vault", Path("/Volumes/NoSuchVolume/models"))
        status = validate_route_status(route)
        assert status.status == ExternalRouteStatus.MOUNT_UNAVAILABLE.value
        assert status.available is False
        assert "NoSuchVolume" in (status.reason or "")

    def test_deep_path_under_missing_mount(self):
        route = _route("deep", Path("/Volumes/MissingDrive/a/b/c/models"))
        status = validate_route_status(route)
        assert status.status == ExternalRouteStatus.MOUNT_UNAVAILABLE.value

    def test_non_volumes_missing_returns_invalid_path(self):
        """A missing path not under /Volumes/ is invalid_path, not mount_unavailable."""
        route = _route("nope", Path("/nonexistent/path/12345"))
        status = validate_route_status(route)
        assert status.status == ExternalRouteStatus.INVALID_PATH.value


# ── Route validation: invalid_path ──────────────────────────────────────────


class TestInvalidPath:
    def test_path_is_file_not_directory(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "not-a-dir"
            f.write_text("hello")
            route = _route("file", f)
            status = validate_route_status(route)
            assert status.status == ExternalRouteStatus.INVALID_PATH.value
            assert "not a directory" in (status.reason or "").lower()


# ── Route ordering ──────────────────────────────────────────────────────────


class TestRouteOrdering:
    def test_sort_by_ascending_priority(self):
        with TemporaryDirectory() as d:
            a = (Path(d) / "a").resolve(); a.mkdir()
            b = (Path(d) / "b").resolve(); b.mkdir()
            c = (Path(d) / "c").resolve(); c.mkdir()
            routes = [
                _route("low", c, priority=30),
                _route("high", a, priority=10),
                _route("mid", b, priority=20),
            ]
            paths = get_available_route_paths(routes)
            assert paths == [a, b, c]  # 10, 20, 30

    def test_equal_priority_sorts_by_id(self):
        with TemporaryDirectory() as d:
            x = (Path(d) / "x").resolve(); x.mkdir()
            y = (Path(d) / "y").resolve(); y.mkdir()
            routes = [
                _route("b", y, priority=50),
                _route("a", x, priority=50),
            ]
            paths = get_available_route_paths(routes)
            assert paths == [x, y]  # "a" before "b"

    def test_disabled_excluded_from_ordering(self):
        with TemporaryDirectory() as d:
            a = (Path(d) / "a").resolve(); a.mkdir()
            b = (Path(d) / "b").resolve(); b.mkdir()
            routes = [
                _route("off", b, enabled=False, priority=1),
                _route("on", a, priority=100),
            ]
            paths = get_available_route_paths(routes)
            assert paths == [a]

    def test_unavailable_excluded_from_ordering(self):
        with TemporaryDirectory() as d:
            a = (Path(d) / "a").resolve(); a.mkdir()
            routes = [
                _route("bad", Path("/Volumes/Ghost/models"), priority=1),
                _route("good", a, priority=100),
            ]
            paths = get_available_route_paths(routes)
            assert paths == [a]


# ── Resolver composition: GGUF ──────────────────────────────────────────────


class TestResolverCompositionGguf:
    def test_resolves_gguf_from_route(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model-q4_k_m.gguf").write_text("gguf")

            route = _route("vault", root)
            req = ModelResolutionRequest(
                model_id="Qwen/Qwen3-14B-GGUF",
                quant="Q4_K_M",
            )
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.FOUND.value
            assert result.source == "external"
            assert result.runtime == "llama_cpp"
            assert "q4_k_m" in (result.path or "").lower()
            assert "vault" in result.metadata.get("route_ids_checked", [])
            assert "vault" in result.metadata.get("available_route_ids", [])

    def test_resolves_gguf_without_quant_from_route(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            route = _route("vault", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.FOUND.value
            assert result.source == "external"


# ── Resolver composition: MLX ──────────────────────────────────────────────


class TestResolverCompositionMlx:
    def test_resolves_mlx_from_route(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            route = _route("vault", root)
            req = ModelResolutionRequest(model_id="mlx-community/Qwen3-14B-4bit")
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.FOUND.value
            assert result.source == "external"
            assert result.runtime == "mlx_lm"


# ── Resolver composition: safetensors ─────────────────────────────────────


class TestResolverCompositionSafetensors:
    def test_resolves_safetensors_from_route(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            route = _route("vault", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B")
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.FOUND.value
            assert result.source == "external"
            assert result.runtime == "unsupported"


# ── Resolver composition: failures ──────────────────────────────────────────


class TestResolverCompositionFailures:
    def test_missing_when_no_routes_available(self):
        route = _route("dead", Path("/Volumes/Ghost/models"), enabled=False)
        req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
        result = resolve_model_from_routes(req, [route])

        assert result.status == ResolutionStatus.MISSING.value
        assert "no external weight routes are available" in (result.reason or "")
        assert "dead" in result.metadata.get("route_ids_checked", [])
        assert result.metadata.get("available_route_ids") == []

    def test_missing_when_model_not_in_route(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()  # Available, but no model content.

            route = _route("vault", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.MISSING.value
            assert result.source is None
            assert "vault" in result.metadata.get("route_ids_checked", [])

    def test_metadata_includes_unavailable_routes(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()
            routes = [
                _route("good", root, priority=10),
                _route("bad", Path("/Volumes/Ghost/models"), priority=5),
                _route("off", Path(d) / "off", enabled=False, priority=1),
            ]
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [routes[1], routes[2]])
            # Only bad and off — neither available.
            unavailable = result.metadata.get("unavailable_routes", [])
            assert len(unavailable) == 2

    def test_missing_when_no_routes_provided(self):
        req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
        result = resolve_model_from_routes(req, [])
        assert result.status == ResolutionStatus.MISSING.value


# ── Metadata enrichment ────────────────────────────────────────────────────


class TestMetadataEnrichment:
    def test_route_ids_checked_present(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            route = _route("primary", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert "route_ids_checked" in result.metadata
            assert result.metadata["route_ids_checked"] == ["primary"]
            assert result.metadata["available_route_ids"] == ["primary"]
            assert result.metadata["unavailable_routes"] == []

    def test_unavailable_route_metadata_shape(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()
            routes = [
                _route("live", root),
                _route("dead", Path("/Volumes/Ghost/models")),
            ]
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, routes)

            unavailable = result.metadata.get("unavailable_routes", [])
            dead = next(r for r in unavailable if r["id"] == "dead")
            assert dead["status"] == ExternalRouteStatus.MOUNT_UNAVAILABLE.value
            assert "path" in dead
            assert "reason" in dead

    def test_checked_paths_still_present(self):
        """Phase 1 checked_paths metadata is preserved in route-aware result."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            route = _route("vault", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert "checked_paths" in result.metadata

    def test_missing_result_still_has_route_metadata(self):
        route = _route("gone", Path("/Volumes/Missing/models"))
        req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
        result = resolve_model_from_routes(req, [route])

        assert result.status == ResolutionStatus.MISSING.value
        assert "route_ids_checked" in result.metadata


# ── Source field ────────────────────────────────────────────────────────────


class TestSourceField:
    def test_external_source_on_hit(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            route = _route("ext", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert result.source == "external"

    def test_source_none_on_miss(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()
            route = _route("ext", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, [route])

            assert result.status == ResolutionStatus.MISSING.value
            assert result.source is None


# ── Const time vs Route priority ────────────────────────────────────────────


class TestFirstHitWins:
    def test_first_priority_route_hit_wins(self):
        with TemporaryDirectory() as d:
            root1 = Path(d) / "primary"
            root2 = Path(d) / "secondary"

            # Model exists in both, but primary should win (lower priority).
            d1 = _mk(root1, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d1 / "primary-model.gguf").write_text("first")
            d2 = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d2 / "secondary-model.gguf").write_text("second")

            routes = [
                _route("secondary", root2, priority=20),
                _route("primary", root1, priority=10),
            ]
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            result = resolve_model_from_routes(req, routes)

            assert result.status == ResolutionStatus.FOUND.value
            assert "primary-model" in (result.path or "")
            assert "secondary-model" not in (result.path or "")


# ── Read-only constraint ────────────────────────────────────────────────────


class TestReadOnly:
    def test_route_validation_does_not_create_directories(self):
        with TemporaryDirectory() as d:
            nonexistent = Path(d) / "does-not-exist-yet"
            route = _route("ro", nonexistent)
            validate_route_status(route)
            assert not nonexistent.exists()

    def test_resolver_does_not_create_directories_on_miss(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()
            route = _route("ro", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            resolve_model_from_routes(req, [route])

            # No gguf/Qwen/... directories were created.
            assert not (root / "gguf").exists()

    def test_resolver_does_not_mutate_route_path(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            gguf_file = gguf_dir / "model.gguf"
            gguf_file.write_text("original")

            mtime_before = gguf_file.stat().st_mtime
            route = _route("ro", root)
            req = ModelResolutionRequest(model_id="Qwen/Qwen3-14B-GGUF")
            resolve_model_from_routes(req, [route])
            mtime_after = gguf_file.stat().st_mtime
            assert mtime_before == mtime_after


# ── Tilde expansion ──────────────────────────────────────────────────────────


class TestTildeExpansion:
    def test_load_expands_tilde(self, monkeypatch):
        monkeypatch.setenv("HOME", "/tmp/fake-home")
        monkeypatch.setenv(
            "WHOOSHD_EXTERNAL_ROUTES",
            json.dumps([{"id": "home", "path": "~/models"}]),
        )
        routes = load_external_weight_routes()
        assert str(routes[0].path).startswith("/tmp/fake-home/models")

    def test_validate_expands_tilde(self):
        route = _route("home", Path("~/models"))
        status = validate_route_status(route)
        # Should not crash — resolves to expanded path.
        assert status.status in (
            ExternalRouteStatus.INVALID_PATH.value,
            ExternalRouteStatus.AVAILABLE.value,
            ExternalRouteStatus.MOUNT_UNAVAILABLE.value,
        )

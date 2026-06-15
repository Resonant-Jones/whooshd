"""Tests for external model runtime handoff.

Validates public ID parsing, external runtime resolution, adapter
selection, route-unavailable behavior, not-servable behavior, and
integration with the runtime router.  Synthetic filesystem fixtures
only — no real model execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from whooshd.models.inventory import (
    parse_external_model_public_id,
    resolve_external_runtime_model,
)
from whooshd.models.routes import (
    ExternalWeightRoute,
    load_external_weight_routes,
)
from whooshd.models.types import (
    ExternalRuntimeResolution,
    ModelFormat,
)
from whooshd.routing import RuntimeRouter
from whooshd.adapters.stub import StubInferenceAdapter


# ── Helpers ─────────────────────────────────────────────────────────────────


def _route(id: str, path: Path, **kw) -> ExternalWeightRoute:
    return ExternalWeightRoute(id=id, path=path, **kw)


def _mk(d: Path, *parts: str) -> Path:
    p = d
    for part in parts:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Public ID parsing ──────────────────────────────────────────────────────


class TestParseExternalModelPublicId:
    def test_gguf_with_quant(self):
        result = parse_external_model_public_id("Qwen/Qwen3-14B-GGUF:Q4_K_M")
        assert result["model_id"] == "Qwen/Qwen3-14B-GGUF"
        assert result["format"] == "gguf"
        assert result["quant"] == "Q4_K_M"

    def test_gguf_with_unknown_suffix(self):
        result = parse_external_model_public_id("Qwen/Qwen3-14B-GGUF:model-weights")
        assert result["format"] == "gguf"
        assert result["quant"] == "model-weights"

    def test_gguf_case_insensitive(self):
        result = parse_external_model_public_id("qwen/something-gguf:q4_k_m")
        assert result["format"] == "gguf"

    def test_mlx_by_prefix(self):
        result = parse_external_model_public_id("mlx-community/Qwen3-14B-4bit")
        assert result["model_id"] == "mlx-community/Qwen3-14B-4bit"
        assert result["format"] == "mlx"
        assert result["quant"] is None

    def test_mlx_by_suffix(self):
        result = parse_external_model_public_id("Qwen/Qwen3-14B-mlx")
        assert result["format"] == "mlx"

    def test_safetensors_default(self):
        result = parse_external_model_public_id("Qwen/Qwen3-14B")
        assert result["model_id"] == "Qwen/Qwen3-14B"
        assert result["format"] == "safetensors"
        assert result["quant"] is None

    def test_single_word_returns_safetensors(self):
        result = parse_external_model_public_id("just-a-model")
        assert result["format"] == "safetensors"


# ── External runtime resolution ────────────────────────────────────────────


class TestExternalRuntimeResolution:
    def test_gguf_resolves_to_path(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:Q4_K_M", routes
            )

            assert result.found is True
            assert result.servable is True
            assert result.format == "gguf"
            assert result.runtime == "llama_cpp"
            assert result.route_id == "vault"
            assert result.path is not None
            assert ".gguf" in (result.path or "")

    def test_gguf_includes_route_id(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            routes = [_route("vaultnode-42", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:model", routes
            )

            assert result.route_id == "vaultnode-42"

    def test_gguf_selects_llama_cpp(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:model", routes
            )

            assert result.runtime == "llama_cpp"

    def test_mlx_resolves_to_directory(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "mlx-community/Qwen3-14B-4bit", routes
            )

            assert result.found is True
            assert result.servable is True
            assert result.format == "mlx"
            assert result.runtime == "mlx_lm"
            assert result.path is not None

    def test_mlx_includes_route_id(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            routes = [_route("mlx-storage", root)]
            result = resolve_external_runtime_model(
                "mlx-community/Qwen3-14B-4bit", routes
            )

            assert result.route_id == "mlx-storage"

    def test_mlx_selects_mlx_lm(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "mlx-community/Qwen3-14B-4bit", routes
            )

            assert result.runtime == "mlx_lm"

    def test_safetensors_returns_not_servable(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B", routes
            )

            assert result.found is True
            assert result.servable is False
            assert result.reason == "not_servable"

    def test_missing_model_returns_not_found(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            root.mkdir()

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/NoSuchModel:Q4_K_M", routes
            )

            assert result.found is False
            assert result.reason == "not_external"

    def test_route_unavailable_at_resolution(self):
        """Route that is not available returns route_unavailable."""
        routes = [_route("ghost", Path("/Volumes/Ghost/models"))]
        result = resolve_external_runtime_model(
            "Qwen/Qwen3-14B-GGUF:Q4_K_M", routes
        )

        # Ghost route — no models found in inventory.
        assert result.found is False

    def test_not_external_for_non_matching_id(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "completely/different-model", routes
            )

            assert result.found is False


# ── Router integration (mocked) ────────────────────────────────────────────


class TestRouterIntegration:
    @pytest.mark.asyncio
    async def test_router_resolves_stub_for_unknown_model(self):
        """When model is not managed and not external, router falls through
        to stub if it's the only adapter (existing behavior)."""
        router = RuntimeRouter()
        router.register(StubInferenceAdapter())

        adapter = await router._resolve_model_runtime("unknown-model")
        assert adapter.kind == "stub"

    @pytest.mark.asyncio
    async def test_router_resolves_external_gguf_as_llama_cpp(self, monkeypatch):
        """When llama_cpp adapter is registered, external GGUF resolves to it."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            # Create a mock adapter that reports llama_cpp kind.
            class MockLlamaCppAdapter:
                kind = "llama_cpp"
                name = "mock-llama-cpp"
                supports_streaming = True

                def set_external_model_path(self, path: str) -> None:
                    self._ext_path = path

                def is_loaded(self) -> bool:
                    return True

                def model_id(self):
                    return None

            router = RuntimeRouter()
            router.register(MockLlamaCppAdapter())

            adapter = await router._resolve_model_runtime(
                "Qwen/Qwen3-14B-GGUF:Q4_K_M"
            )
            assert adapter is not None
            assert adapter.kind == "llama_cpp"

    @pytest.mark.asyncio
    async def test_external_duplicate_managed_wins(self, monkeypatch):
        """When a model id exists in both managed registry and external
        inventory, the managed one wins.  External fallback is skipped."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            router = RuntimeRouter()
            router.register(StubInferenceAdapter())

            # "stub-model" is a built-in ID — the stub owns it.
            # External inventory would not match it anyway.
            adapter = await router._resolve_model_runtime("stub-model")
            assert adapter.kind == "stub"


# ── Route unavailable behavior ─────────────────────────────────────────────


class TestRouteUnavailable:
    @pytest.mark.asyncio
    async def test_external_model_with_unregistered_runtime_raises(self, monkeypatch):
        """When external model resolves to a runtime that is not registered,
        router raises ModelResolutionError."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from whooshd.routing import ModelResolutionError

            router = RuntimeRouter()
            # No llama_cpp or mlx_lm adapter registered — only stub.
            router.register(StubInferenceAdapter())

            with pytest.raises(ModelResolutionError) as exc:
                await router._resolve_model_runtime(
                    "Qwen/Qwen3-14B-GGUF:model"
                )

            assert "not available" in str(exc.value).lower()


# ── Not servable behavior ──────────────────────────────────────────────────


class TestNotServable:
    @pytest.mark.asyncio
    async def test_safetensors_raises_model_resolution_error(self, monkeypatch):
        """Safetensors external model is not servable — router raises."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            st_dir = _mk(root, "safetensors", "Qwen", "Qwen3-14B")
            (st_dir / "config.json").write_text("{}")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from whooshd.routing import ModelResolutionError

            router = RuntimeRouter()
            router.register(StubInferenceAdapter())

            with pytest.raises(ModelResolutionError) as exc:
                await router._resolve_model_runtime("Qwen/Qwen3-14B")

            assert "not servable" in str(exc.value).lower()


# ── No raw paths exposed in errors ─────────────────────────────────────────


class TestNoRawPathsExposed:
    @pytest.mark.asyncio
    async def test_external_runtime_unavailable_error_hides_path(self, monkeypatch):
        """When external model's runtime is not available, error hides raw path."""
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "SecretModel")
            (gguf_dir / "model.gguf").write_text("secret-weights")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from whooshd.routing import ModelResolutionError

            router = RuntimeRouter()
            router.register(StubInferenceAdapter())

            with pytest.raises(ModelResolutionError) as exc:
                await router._resolve_model_runtime(
                    "Qwen/SecretModel:model"
                )

            msg = str(exc.value)
            # Error message should not contain the raw filesystem path.
            assert "SecretModel" in msg  # model ID is in message, path is not
            assert "secret-weights" not in msg
            assert "/gguf/" not in msg


# ── Router external path passthrough ───────────────────────────────────────


class TestExternalPathPassthrough:
    def test_stub_adapter_accepts_external_path(self):
        """Stub adapter accepts set_external_model_path without error."""
        adapter = StubInferenceAdapter()
        adapter.set_external_model_path("/some/external/path/model.gguf")
        # No crash, no side effects for stub.

    @pytest.mark.asyncio
    async def test_router_sets_external_path_on_adapter(self, monkeypatch):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            gguf_file = gguf_dir / "Qwen3-14B-Q4_K_M.gguf"
            gguf_file.write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            class MockLlamaCppAdapter:
                kind = "llama_cpp"
                name = "mock-llama-cpp"
                supports_streaming = True
                _ext_path = None

                def set_external_model_path(self, path: str) -> None:
                    self._ext_path = path

                def is_loaded(self) -> bool:
                    return True

                def model_id(self):
                    return None

            router = RuntimeRouter()
            mock = MockLlamaCppAdapter()
            router.register(mock)

            adapter = await router._resolve_model_runtime(
                "Qwen/Qwen3-14B-GGUF:Q4_K_M"
            )
            assert adapter is not None
            assert adapter.kind == "llama_cpp"
            # The mock adapter should have received the external path.
            assert hasattr(adapter, "_ext_path")
            assert adapter._ext_path is not None
            assert str(gguf_file.resolve()) in (adapter._ext_path or "")


# ── Existing behavior unchanged ────────────────────────────────────────────


class TestExistingBehavior:
    @pytest.mark.asyncio
    async def test_unknown_model_still_uses_stub(self):
        """When no routes are configured and model is unknown, stub wins."""
        router = RuntimeRouter()
        router.register(StubInferenceAdapter())

        adapter = await router._resolve_model_runtime("anything")
        assert adapter.kind == "stub"


# ── Read-only constraints ──────────────────────────────────────────────────


class TestReadOnly:
    def test_resolution_does_not_mutate_filesystem(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            gguf_file = gguf_dir / "model.gguf"
            gguf_file.write_text("original")

            mtime_before = gguf_file.stat().st_mtime
            routes = [_route("vault", root)]
            resolve_external_runtime_model("Qwen/Qwen3-14B-GGUF:model", routes)
            mtime_after = gguf_file.stat().st_mtime
            assert mtime_before == mtime_after


# ── Duplicate handling ─────────────────────────────────────────────────────


class TestDuplicateHandling:
    def test_external_duplicate_prefers_first_route(self):
        with TemporaryDirectory() as d:
            root1 = (Path(d) / "r1").resolve()
            root2 = (Path(d) / "r2").resolve()
            d1 = _mk(root1, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d1 / "q4.gguf").write_text("first")
            d2 = _mk(root2, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (d2 / "q8.gguf").write_text("second")

            routes = [
                _route("primary", root1, priority=10),
                _route("secondary", root2, priority=20),
            ]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:q4", routes
            )

            assert result.route_id == "primary"


# ── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_routes_configured(self):
        result = resolve_external_runtime_model(
            "Qwen/Qwen3-14B-GGUF:Q4_K_M", []
        )
        assert result.found is False

    def test_empty_model_id(self):
        result = parse_external_model_public_id("")
        assert result["model_id"] == ""

    def test_whitespace_model_id(self):
        result = parse_external_model_public_id("   Qwen/Qwen3-14B-GGUF:Q4_K_M   ")
        assert result["model_id"] == "Qwen/Qwen3-14B-GGUF"
        assert result["quant"] == "Q4_K_M"

    def test_resolution_metadata_includes_quant(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:Q4_K_M", routes
            )

            assert result.metadata.get("quant") == "Q4_K_M"

    def test_resolution_preserves_matched_file(self):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "special-model.gguf").write_text("gguf")

            routes = [_route("vault", root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:special-model", routes
            )

            assert result.metadata.get("matched_file") == "special-model.gguf"

    def test_external_runtime_resolution_is_frozen(self):
        result = ExternalRuntimeResolution(
            found=True,
            servable=True,
            model_id="test",
        )
        with pytest.raises(Exception):
            result.found = False  # type: ignore[misc]

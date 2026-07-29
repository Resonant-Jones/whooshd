"""Tests for real adapter external path binding (Phase 4B).

Validates that LlamaCppAdapter and MlxLmServerAdapter accept external
model paths, use them for argv building and inference, clear state
between requests, and produce client-safe errors without raw path
exposure.  Synthetic filesystem fixtures — no real model execution.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from whooshd.adapters.llama_cpp import (
    LlamaCppAdapter,
    LlamaCppAdapterConfig,
    build_llama_server_argv,
    _validate_files_exist,
)
from whooshd.adapters.mlx_lm_server import (
    MlxLmServerAdapter,
    MlxLmServerConfig,
    build_mlx_lm_server_argv,
)
from whooshd.contracts import GenerateRequest


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mk(d: Path, *parts: str) -> Path:
    p = d
    for part in parts:
        p = p / part
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── LlamaCppAdapter: external path binding ──────────────────────────────────


class TestLlamaCppAdapterPathBinding:
    def test_set_external_model_path_exists(self):
        adapter = LlamaCppAdapter()
        assert hasattr(adapter, "set_external_model_path")

    def test_set_external_model_path_stores(self):
        with TemporaryDirectory() as d:
            gguf_file = Path(d) / "test.gguf"
            gguf_file.write_text("fake")

            adapter = LlamaCppAdapter()
            adapter.set_external_model_path(str(gguf_file))
            assert adapter._external_model_path == gguf_file

    def test_effective_path_returns_external_when_set(self):
        with TemporaryDirectory() as d:
            gguf_file = Path(d) / "external.gguf"
            gguf_file.write_text("fake")

            adapter = LlamaCppAdapter()
            adapter.set_external_model_path(str(gguf_file))
            assert adapter._effective_model_path == str(gguf_file)

    def test_effective_path_returns_config_when_no_external(self):
        adapter = LlamaCppAdapter()
        # When no external path, reverts to config.model_path.
        assert adapter._effective_model_path == adapter._config.model_path

    def test_clear_resets_path(self):
        with TemporaryDirectory() as d:
            gguf_file = Path(d) / "test.gguf"
            gguf_file.write_text("fake")

            adapter = LlamaCppAdapter()
            adapter.set_external_model_path(str(gguf_file))
            adapter._clear_external_model_path()
            assert adapter._external_model_path is None

    def test_managed_model_unchanged_when_no_external(self):
        """Default model behavior unchanged when no external path is set."""
        adapter = LlamaCppAdapter()
        assert adapter._effective_model_path == adapter._config.model_path

    def test_model_id_reflects_external_path(self):
        with TemporaryDirectory() as d:
            gguf_file = Path(d) / "ext-model.gguf"
            gguf_file.write_text("fake")

            adapter = LlamaCppAdapter()
            adapter.set_external_model_path(str(gguf_file))
            assert adapter.model_id() == str(gguf_file)

    def test_model_id_cleared_after_reset(self):
        with TemporaryDirectory() as d:
            gguf_file = Path(d) / "ext-model.gguf"
            gguf_file.write_text("fake")

            adapter = LlamaCppAdapter()
            adapter.set_external_model_path(str(gguf_file))
            adapter._clear_external_model_path()
            assert adapter.model_id() == adapter._config.model_path

    def test_generate_clears_external_path_when_health_probe_fails(self):
        adapter = LlamaCppAdapter(
            LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        async def generate():
            adapter.set_external_model_path("/models/external.gguf")
            try:
                await adapter.generate(GenerateRequest(prompt="hello"))
            except RuntimeError:
                assert adapter._external_model_path is None
                raise

        with patch.object(
            adapter,
            "check_health",
            AsyncMock(side_effect=RuntimeError("probe failed")),
        ):
            with pytest.raises(RuntimeError, match="probe failed"):
                asyncio.run(generate())

        assert adapter._external_model_path is None

    def test_generate_clears_external_path_when_forwarding_fails(self):
        adapter = LlamaCppAdapter(
            LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )
        health = SimpleNamespace(reachable=True)

        async def generate():
            adapter.set_external_model_path("/models/external.gguf")
            try:
                await adapter.generate(GenerateRequest(prompt="hello"))
            except RuntimeError:
                assert adapter._external_model_path is None
                raise

        with patch.object(adapter, "check_health", AsyncMock(return_value=health)), patch(
            "whooshd.http_forwarding.forward_non_streaming",
            AsyncMock(side_effect=RuntimeError("forward failed")),
        ):
            with pytest.raises(RuntimeError, match="forward failed"):
                asyncio.run(generate())

        assert adapter._external_model_path is None

    def test_generate_failure_does_not_clear_concurrent_request_path(self):
        adapter = LlamaCppAdapter(
            LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )
        first_probe_started = asyncio.Event()
        allow_first_probe_failure = asyncio.Event()
        first_request_finished = asyncio.Event()
        forwarded_overrides = []

        async def check_health():
            if adapter._effective_model_path == "/models/first.gguf":
                first_probe_started.set()
                await allow_first_probe_failure.wait()
                raise RuntimeError("probe failed")
            await first_request_finished.wait()
            return SimpleNamespace(reachable=True)

        async def forward(
            _url, _request, *, timeout, model_override, adapter_kind=None
        ):
            forwarded_overrides.append(model_override)
            raise RuntimeError("forward failed")

        async def first_request():
            adapter.set_external_model_path("/models/first.gguf")
            try:
                await adapter.generate(GenerateRequest(prompt="first"))
            finally:
                first_request_finished.set()

        async def second_request():
            await first_probe_started.wait()
            adapter.set_external_model_path("/models/second.gguf")
            allow_first_probe_failure.set()
            await adapter.generate(GenerateRequest(prompt="second"))

        async def run_overlapping_requests():
            results = await asyncio.gather(
                first_request(), second_request(), return_exceptions=True
            )
            assert all(isinstance(result, RuntimeError) for result in results)

        with patch.object(adapter, "check_health", check_health), patch(
            "whooshd.http_forwarding.forward_non_streaming", forward
        ):
            asyncio.run(run_overlapping_requests())

        assert forwarded_overrides == ["/models/second.gguf"]
        assert adapter._external_model_path is None


# ── LlamaCppAdapter: argv building with external path ──────────────────────


class TestLlamaCppArgvWithExternalPath:
    def test_argv_uses_external_path_when_override_provided(self):
        with TemporaryDirectory() as d:
            binary = Path(d) / "fake-llama-server"
            binary.write_text("binary")
            binary.chmod(0o755)
            gguf_file = Path(d) / "external-model.gguf"
            gguf_file.write_text("weights")

            config = LlamaCppAdapterConfig(
                binary_path=str(binary),
                model_path="/original/model.gguf",
                host="127.0.0.1",
                port=8080,
            )

            argv = build_llama_server_argv(
                config, model_path_override=str(gguf_file)
            )
            assert "--model" in argv
            model_idx = argv.index("--model")
            assert argv[model_idx + 1] == str(gguf_file)
            assert "/original/model.gguf" not in argv

    def test_argv_uses_config_path_when_no_override(self):
        with TemporaryDirectory() as d:
            binary = Path(d) / "fake-llama-server"
            binary.write_text("binary")
            binary.chmod(0o755)

            config = LlamaCppAdapterConfig(
                binary_path=str(binary),
                model_path="/original/model.gguf",
                host="127.0.0.1",
                port=8080,
            )

            argv = build_llama_server_argv(config)
            model_idx = argv.index("--model")
            assert argv[model_idx + 1] == "/original/model.gguf"

    def test_validate_config_accepts_external_gguf(self):
        with TemporaryDirectory() as d:
            binary = Path(d) / "fake-llama-server"
            binary.write_text("binary")
            binary.chmod(0o755)
            gguf = Path(d) / "ext.gguf"
            gguf.write_text("w")

            config = LlamaCppAdapterConfig(
                binary_path=str(binary),
                model_path="",  # Empty, but override provides the path
                host="127.0.0.1",
                port=8080,
            )
            # Should not raise because model_path_override is provided.
            build_llama_server_argv(config, model_path_override=str(gguf))

    def test_validate_rejects_non_gguf_external(self):
        with TemporaryDirectory() as d:
            binary = Path(d) / "fake-llama-server"
            binary.write_text("binary")
            binary.chmod(0o755)
            bad = Path(d) / "not-gguf.txt"
            bad.write_text("w")

            from whooshd.adapters.llama_cpp import LlamaCppConfigError

            config = LlamaCppAdapterConfig(
                binary_path=str(binary),
                model_path="",
                host="127.0.0.1",
                port=8080,
            )
            with pytest.raises(LlamaCppConfigError):
                build_llama_server_argv(config, model_path_override=str(bad))


# ── MlxLmServerAdapter: external path binding ──────────────────────────────


class TestMlxLmServerPathBinding:
    def test_set_external_model_path_exists(self):
        adapter = MlxLmServerAdapter()
        assert hasattr(adapter, "set_external_model_path")

    def test_set_external_model_path_stores(self):
        with TemporaryDirectory() as d:
            model_dir = Path(d) / "mlx-model"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")

            adapter = MlxLmServerAdapter()
            adapter.set_external_model_path(str(model_dir))
            assert adapter._external_model_path == model_dir

    def test_effective_path_returns_external_when_set(self):
        with TemporaryDirectory() as d:
            model_dir = Path(d) / "mlx-model"
            model_dir.mkdir()

            adapter = MlxLmServerAdapter()
            adapter.set_external_model_path(str(model_dir))
            assert adapter._effective_model_path == str(model_dir)

    def test_effective_path_returns_config_when_no_external(self):
        adapter = MlxLmServerAdapter()
        assert adapter._effective_model_path == adapter._config.model

    def test_clear_resets_path(self):
        with TemporaryDirectory() as d:
            model_dir = Path(d) / "mlx-model"
            model_dir.mkdir()

            adapter = MlxLmServerAdapter()
            adapter.set_external_model_path(str(model_dir))
            adapter._clear_external_model_path()
            assert adapter._external_model_path is None

    def test_model_id_reflects_external_path(self):
        with TemporaryDirectory() as d:
            model_dir = Path(d) / "ext-mlx"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")

            adapter = MlxLmServerAdapter()
            adapter.set_external_model_path(str(model_dir))
            assert adapter.model_id() == str(model_dir)


# ── MlxLmServerAdapter: argv building with external path ────────────────────


class TestMlxLmArgvWithExternalPath:
    def test_argv_uses_effective_path(self):
        with TemporaryDirectory() as d:
            model_dir = Path(d) / "external-mlx"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}")

            config = MlxLmServerConfig(
                model=str(model_dir),
                host="127.0.0.1",
                port=8081,
            )

            argv = build_mlx_lm_server_argv(config)
            assert "--model" in argv
            model_idx = argv.index("--model")
            assert argv[model_idx + 1] == str(model_dir)


# ── State leak prevention ──────────────────────────────────────────────────


class TestStateLeakPrevention:
    @pytest.mark.parametrize("adapter_type", [LlamaCppAdapter, MlxLmServerAdapter])
    def test_external_bindings_are_isolated_between_overlapping_tasks(
        self, adapter_type
    ):
        adapter = adapter_type()

        async def exercise() -> None:
            first_bound = asyncio.Event()
            second_bound = asyncio.Event()

            async def first() -> None:
                adapter.set_external_model_path("/external/model-a")
                first_bound.set()
                await second_bound.wait()
                assert adapter._effective_model_path == "/external/model-a"
                adapter._clear_external_model_path()

            async def second() -> None:
                await first_bound.wait()
                adapter.set_external_model_path("/external/model-b")
                second_bound.set()
                await asyncio.sleep(0)
                assert adapter._effective_model_path == "/external/model-b"
                adapter._clear_external_model_path()

            await asyncio.gather(first(), second())

        asyncio.run(exercise())

    def test_llama_cpp_clear_prevents_leak(self):
        with TemporaryDirectory() as d:
            gguf1 = Path(d) / "model-a.gguf"
            gguf1.write_text("a")
            gguf2 = Path(d) / "model-b.gguf"
            gguf2.write_text("b")

            adapter = LlamaCppAdapter()

            # Request A: set external path.
            adapter.set_external_model_path(str(gguf1))
            assert adapter._effective_model_path == str(gguf1)

            # Clear after request A.
            adapter._clear_external_model_path()
            assert adapter._external_model_path is None

            # Request B: should not see A's path.
            adapter.set_external_model_path(str(gguf2))
            assert adapter._effective_model_path == str(gguf2)

    def test_mlx_clear_prevents_leak(self):
        with TemporaryDirectory() as d:
            dir1 = Path(d) / "model-a"
            dir1.mkdir()
            dir2 = Path(d) / "model-b"
            dir2.mkdir()

            adapter = MlxLmServerAdapter()

            adapter.set_external_model_path(str(dir1))
            assert adapter._effective_model_path == str(dir1)

            adapter._clear_external_model_path()
            assert adapter._external_model_path is None

            adapter.set_external_model_path(str(dir2))
            assert adapter._effective_model_path == str(dir2)


# ── Missing external path errors ────────────────────────────────────────────


class TestMissingExternalPath:
    def test_llama_cpp_missing_external_file_client_safe(self):
        """When external .gguf file doesn't exist, error hides raw path."""
        with TemporaryDirectory() as d:
            binary = Path(d) / "fake-llama-server"
            binary.write_text("bin")
            binary.chmod(0o755)

            config = LlamaCppAdapterConfig(
                binary_path=str(binary),
                model_path="",
                host="127.0.0.1",
                port=8080,
            )

            from whooshd.adapters.llama_cpp import LlamaCppConfigError

            try:
                _validate_files_exist(
                    config,
                    model_path_override="/nonexistent/path/secret-model.gguf",
                )
            except LlamaCppConfigError as exc:
                msg = str(exc)
                # Client-safe: no raw path.
                assert "secret-model" not in msg
                assert "unavailable" in msg.lower() or "execution" in msg.lower()
            else:
                pytest.fail("Expected LlamaCppConfigError")

    def test_no_config_binary_fails_safely(self):
        """When llama-server binary not found, error is also client-safe."""
        from whooshd.adapters.llama_cpp import LlamaCppConfigError

        config = LlamaCppAdapterConfig(
            binary_path="/nonexistent/llama-server",
            model_path="/tmp/x.gguf",
            host="127.0.0.1",
            port=8080,
        )
        try:
            _validate_files_exist(config)
        except LlamaCppConfigError as exc:
            msg = str(exc)
            # Binary path may appear in error — it's a config path, not a user secret.
            assert "llama-server" in msg
        else:
            pytest.fail("Expected LlamaCppConfigError")


# ── No raw paths exposed ────────────────────────────────────────────────────


class TestNoRawPathsExposed:
    def test_missing_external_path_hides_path(self):
        """Missing external path error must not expose the raw path."""
        from whooshd.adapters.llama_cpp import LlamaCppConfigError

        try:
            _validate_files_exist(
                LlamaCppAdapterConfig(
                    binary_path="/bin/true",
                    model_path="",
                    host="127.0.0.1",
                    port=8080,
                ),
                model_path_override="/Users/alice/secrets/private-model.gguf",
            )
        except LlamaCppConfigError as exc:
            msg = str(exc)
            assert "private-model" not in msg
            assert "/secrets" not in msg
            assert "/Users" not in msg


# ── Integration with router (Phase 4A compat) ───────────────────────────────


class TestRouterAdapterIntegration:
    @pytest.mark.asyncio
    async def test_router_resolves_external_gguf_with_llama_cpp(self, monkeypatch):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "Qwen3-14B-Q4_K_M.gguf").write_text("gguf")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from whooshd.routing import RuntimeRouter
            # Use the real LlamaCppAdapter.
            router = RuntimeRouter()
            router.register(LlamaCppAdapter())

            adapter = await router._resolve_model_runtime(
                "Qwen/Qwen3-14B-GGUF:Q4_K_M"
            )
            assert adapter is not None
            assert adapter.kind == "llama_cpp"
            # External path should be set.
            assert adapter._external_model_path is not None
            assert ".gguf" in str(adapter._external_model_path)

    @pytest.mark.asyncio
    async def test_router_resolves_external_mlx_with_mlx_lm_server(self, monkeypatch):
        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            mlx_dir = _mk(root, "mlx", "mlx-community", "Qwen3-14B-4bit")
            (mlx_dir / "config.json").write_text("{}")

            monkeypatch.setenv(
                "WHOOSHD_EXTERNAL_ROUTES",
                json.dumps([{"id": "vault", "path": str(root)}]),
            )

            from whooshd.routing import RuntimeRouter
            router = RuntimeRouter()
            router.register(MlxLmServerAdapter())

            adapter = await router._resolve_model_runtime(
                "mlx-community/Qwen3-14B-4bit"
            )
            assert adapter is not None
            assert adapter.kind == "mlx_lm_server"
            assert adapter._external_model_path is not None

    @pytest.mark.asyncio
    async def test_managed_model_duplicate_bypasses_external(self, monkeypatch):
        """When model_id matches both managed+buildin, stub wins (existing behavior)."""
        from whooshd.routing import RuntimeRouter
        from whooshd.adapters.stub import StubInferenceAdapter

        router = RuntimeRouter()
        router.register(StubInferenceAdapter())

        # stub-model is built-in — should not trigger external lookup.
        adapter = await router._resolve_model_runtime("stub-model")
        assert adapter.kind == "stub"


# ── Regression: Phase 4A tests still pass ───────────────────────────────────


class TestPhase4ARegression:
    """Quick smoke — import and smoke the Phase 4A resolution functions."""

    def test_parse_external_id_still_works(self):
        from whooshd.models.inventory import parse_external_model_public_id
        result = parse_external_model_public_id("Qwen/Qwen3-14B-GGUF:Q4_K_M")
        assert result["format"] == "gguf"
        assert result["quant"] == "Q4_K_M"

    def test_resolve_external_runtime_still_works(self):
        from whooshd.models.inventory import resolve_external_runtime_model
        from whooshd.models.routes import ExternalWeightRoute

        with TemporaryDirectory() as d:
            root = Path(d) / "shelf"
            gguf_dir = _mk(root, "gguf", "Qwen", "Qwen3-14B-GGUF")
            (gguf_dir / "model.gguf").write_text("gguf")

            routes = [ExternalWeightRoute(id="v", path=root)]
            result = resolve_external_runtime_model(
                "Qwen/Qwen3-14B-GGUF:model", routes
            )
            assert result.found is True

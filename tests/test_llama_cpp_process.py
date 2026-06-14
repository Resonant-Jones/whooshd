"""Tests for llama.cpp process supervision (ManagedLlamaServer).

Covers: argv building, config validation, ManagedLlamaServer lifecycle,
health state mapping for supervised processes, warmup/unload behaviour,
idempotency, timeout handling, graceful shutdown escalation.

No real llama.cpp binary or subprocess is launched — all process
operations are mocked via subprocess.Popen patches.
"""

from __future__ import annotations

import os
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whooshd.adapters.llama_cpp import (
    LlamaCppAdapter,
    LlamaCppAdapterConfig,
    LlamaCppConfigError,
    LlamaCppProcessError,
    ManagedLlamaServer,
    _LlamaCppHealthStatus,
    _LlamaCppNotImplementedError,
    _validate_managed_config,
    build_llama_server_argv,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_config() -> LlamaCppAdapterConfig:
    return LlamaCppAdapterConfig(
        binary_path="/usr/local/bin/llama-server",
        model_path="/models/test-model.gguf",
        host="127.0.0.1",
        port=8080,
        auto_start=True,
        startup_timeout_seconds=5.0,
        health_timeout_seconds=1.0,
    )


@pytest.fixture
def mock_subprocess():
    """Return a MagicMock that behaves like a running Popen object.

    Uses a plain MagicMock (no spec) so instance attributes like
    pid and returncode are always accessible.
    """
    proc = MagicMock()
    proc.poll.return_value = None  # Running
    proc.pid = 12345
    proc.returncode = None
    return proc


# ── argv building ────────────────────────────────────────────────────────────


class TestBuildArgv:
    def test_basic_argv_includes_required_args(self, valid_config):
        argv = build_llama_server_argv(valid_config)
        assert argv[0] == "/usr/local/bin/llama-server"
        assert argv[1] == "--model"
        assert argv[2] == "/models/test-model.gguf"
        assert argv[3] == "--host"
        assert argv[4] == "127.0.0.1"
        assert argv[5] == "--port"
        assert argv[6] == "8080"

    def test_includes_optional_context_window(self, valid_config):
        valid_config.context_window = 32768
        argv = build_llama_server_argv(valid_config)
        assert "--ctx-size" in argv
        idx = argv.index("--ctx-size")
        assert argv[idx + 1] == "32768"

    def test_includes_optional_parallel_slots(self, valid_config):
        valid_config.parallel_slots = 4
        argv = build_llama_server_argv(valid_config)
        assert "--parallel" in argv
        idx = argv.index("--parallel")
        assert argv[idx + 1] == "4"

    def test_appends_extra_args(self, valid_config):
        valid_config.extra_args = ["--mlock", "--no-mmap"]
        argv = build_llama_server_argv(valid_config)
        assert "--mlock" in argv
        assert "--no-mmap" in argv
        # extra_args should be last.
        assert argv[-2] == "--mlock"
        assert argv[-1] == "--no-mmap"

    def test_skips_optional_args_when_not_set(self, valid_config):
        valid_config.context_window = None
        valid_config.parallel_slots = None
        argv = build_llama_server_argv(valid_config)
        assert "--ctx-size" not in argv
        assert "--parallel" not in argv

    def test_all_args_are_strings(self, valid_config):
        """Build argv returns only strings — no shell=True behaviour."""
        argv = build_llama_server_argv(valid_config)
        for arg in argv:
            assert isinstance(arg, str)

    def test_never_uses_shell_string(self, valid_config):
        """argv list, not a single shell string."""
        argv = build_llama_server_argv(valid_config)
        assert isinstance(argv, list)
        assert len(argv) > 1  # Must be multiple tokens.


# ── Config validation ────────────────────────────────────────────────────────


class TestValidateManagedConfig:
    def test_valid_config_passes(self, valid_config):
        # Mock filesystem checks so binary/model exist.
        with patch("os.path.isfile", return_value=True):
            _validate_managed_config(valid_config)  # Should not raise

    def test_missing_binary_path_raises(self):
        config = LlamaCppAdapterConfig(binary_path=None, model_path="/m.gguf")
        with pytest.raises(LlamaCppConfigError, match="binary_path"):
            _validate_managed_config(config)

    def test_missing_model_path_raises(self):
        config = LlamaCppAdapterConfig(binary_path="/bin/llama-server", model_path=None)
        with pytest.raises(LlamaCppConfigError, match="model_path"):
            _validate_managed_config(config)

    def test_binary_not_found_raises(self):
        config = LlamaCppAdapterConfig(
            binary_path="/nonexistent/llama-server", model_path="/m.gguf"
        )
        with patch("os.path.isfile", side_effect=lambda p: "llama-server" not in p):
            from whooshd.adapters.llama_cpp import _validate_files_exist
            with pytest.raises(LlamaCppConfigError, match="binary not found"):
                _validate_files_exist(config)

    def test_model_not_found_raises(self):
        config = LlamaCppAdapterConfig(
            binary_path="/bin/llama-server", model_path="/nonexistent/m.gguf"
        )
        with patch("os.path.isfile", side_effect=lambda p: "llama-server" in p):
            from whooshd.adapters.llama_cpp import _validate_files_exist
            with pytest.raises(LlamaCppConfigError, match="Model file not found"):
                _validate_files_exist(config)

    def test_non_gguf_model_path_raises(self, valid_config):
        valid_config.model_path = "/models/model.bin"
        with patch("os.path.isfile", return_value=True):
            with pytest.raises(LlamaCppConfigError, match="must end in .gguf"):
                _validate_managed_config(valid_config)

    def test_invalid_port_raises(self, valid_config):
        valid_config.port = 0
        with pytest.raises(LlamaCppConfigError, match="valid range"):
            _validate_managed_config(valid_config)

    def test_port_too_high_raises(self, valid_config):
        valid_config.port = 99999
        with pytest.raises(LlamaCppConfigError, match="valid range"):
            _validate_managed_config(valid_config)

    def test_zero_context_window_raises(self, valid_config):
        valid_config.context_window = 0
        with patch("os.path.isfile", return_value=True):
            with pytest.raises(LlamaCppConfigError, match="context_window"):
                _validate_managed_config(valid_config)

    def test_negative_parallel_slots_raises(self, valid_config):
        valid_config.parallel_slots = -1
        with patch("os.path.isfile", return_value=True):
            with pytest.raises(LlamaCppConfigError, match="parallel_slots"):
                _validate_managed_config(valid_config)


# ── ManagedLlamaServer — start ──────────────────────────────────────────────


class TestManagedProcessStart:
    def test_start_launches_subprocess(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess) as popen_mock:
            result = proc_wrapper.start()

        popen_mock.assert_called_once()
        assert result is mock_subprocess
        assert proc_wrapper.is_running is True
        assert proc_wrapper.pid == 12345

    def test_start_uses_shell_false(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess) as popen_mock:
            proc_wrapper.start()

        call_kwargs = popen_mock.call_args.kwargs
        assert call_kwargs.get("shell") is False or call_kwargs.get("shell") is None

    def test_start_raises_when_already_running(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        with pytest.raises(LlamaCppProcessError, match="already running"):
            proc_wrapper.start()

    def test_start_handles_os_error(self, valid_config):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", side_effect=OSError("no such file")):
            with pytest.raises(LlamaCppProcessError, match="Failed to launch"):
                proc_wrapper.start()


# ── ManagedLlamaServer — stop ───────────────────────────────────────────────


class TestManagedProcessStop:
    def test_stop_terminates_running_process(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        proc_wrapper.stop(timeout=1.0)
        mock_subprocess.terminate.assert_called_once()
        mock_subprocess.wait.assert_called_once_with(timeout=1.0)

    def test_stop_is_safe_when_not_started(self, valid_config):
        proc_wrapper = ManagedLlamaServer(valid_config)
        proc_wrapper.stop()  # Should not raise

    def test_stop_safe_when_already_exited(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        # Simulate process exit.
        mock_subprocess.poll.return_value = 0
        proc_wrapper.stop()  # Should clean up silently.

    def test_stop_escalates_to_kill(self, valid_config, mock_subprocess):
        """If terminate doesn't work, kill after timeout."""
        # Only first wait() raises; second succeeds.
        mock_subprocess.wait.side_effect = [
            subprocess.TimeoutExpired("wait", 1.0),
            None,
        ]

        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        proc_wrapper.stop(timeout=1.0)
        mock_subprocess.terminate.assert_called_once()
        mock_subprocess.kill.assert_called_once()


# ── ManagedLlamaServer — restart ────────────────────────────────────────────


class TestManagedProcessRestart:
    def test_restart_stops_then_starts(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        # Replace with a fresh mock for the restart.
        new_mock = MagicMock()
        new_mock.poll.return_value = None
        new_mock.pid = 67890

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=new_mock) as popen2:
            result = proc_wrapper.restart()

        mock_subprocess.terminate.assert_called_once()
        assert result is new_mock
        assert proc_wrapper.pid == 67890


# ── ManagedLlamaServer — check_exited ───────────────────────────────────────


class TestManagedProcessCheckExited:
    def test_check_exited_detects_crash(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        mock_subprocess.poll.return_value = 1  # Exited with error
        assert proc_wrapper.check_exited() is True
        assert proc_wrapper.is_running is False

    def test_check_exited_clears_process_handle(self, valid_config, mock_subprocess):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        mock_subprocess.poll.return_value = 1
        proc_wrapper.check_exited()
        assert proc_wrapper.pid is None


# ── ManagedLlamaServer — wait_until_ready ───────────────────────────────────


class TestManagedProcessWaitUntilReady:
    async def test_waits_until_health_reports_reachable(
        self, valid_config, mock_subprocess
    ):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        # Health fn returns reachable on second call.
        call_count = 0

        async def _health_fn():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return _LlamaCppHealthStatus(
                    reachable=True,
                    runner_status="ready",
                    model_lifecycle="ready",
                    detail="ready",
                )
            return _LlamaCppHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="warmup",
                detail="warming",
            )

        await proc_wrapper.wait_until_ready(
            health_fn=_health_fn,
            startup_timeout=5.0,
            probe_interval=0.01,
        )
        assert call_count >= 2

    async def test_raises_on_process_exit_before_ready(
        self, valid_config, mock_subprocess
    ):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        mock_subprocess.poll.return_value = 1  # Simulate crash

        async def _health_fn():
            return _LlamaCppHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="warmup",
                detail="warming",
            )

        with pytest.raises(LlamaCppProcessError, match="exited unexpectedly"):
            await proc_wrapper.wait_until_ready(
                health_fn=_health_fn,
                startup_timeout=5.0,
                probe_interval=0.01,
            )

    async def test_raises_on_startup_timeout(
        self, valid_config, mock_subprocess
    ):
        proc_wrapper = ManagedLlamaServer(valid_config)
        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_subprocess):
            proc_wrapper.start()

        async def _health_fn():
            return _LlamaCppHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="warmup",
                detail="still warming",
            )

        with pytest.raises(LlamaCppProcessError, match="did not become ready"):
            await proc_wrapper.wait_until_ready(
                health_fn=_health_fn,
                startup_timeout=0.05,
                probe_interval=0.01,
            )


# ── Adapter lifecycle — managed mode ────────────────────────────────────────


class TestAdapterManagedWarmup:
    def _make_managed_config(self) -> LlamaCppAdapterConfig:
        return LlamaCppAdapterConfig(
            binary_path="/usr/local/bin/llama-server",
            model_path="/models/test.gguf",
            auto_start=True,
            startup_timeout_seconds=5.0,
            health_timeout_seconds=1.0,
        )

    def test_warmup_validates_missing_binary(self):
        """auto_start=true with missing binary_path must fail."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path=None,
                model_path="/m.gguf",
                auto_start=True,
            )
        )
        import asyncio
        with pytest.raises(LlamaCppConfigError, match="binary_path"):
            asyncio.run(adapter.warmup())

    def test_warmup_validates_missing_model(self):
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path="/bin/llama-server",
                model_path=None,
                auto_start=True,
            )
        )
        import asyncio
        with pytest.raises(LlamaCppConfigError, match="model_path"):
            asyncio.run(adapter.warmup())

    def test_warmup_starts_process(self):
        config = self._make_managed_config()
        adapter = LlamaCppAdapter(config=config)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999

        health_ready = _LlamaCppHealthStatus(
            reachable=True,
            runner_status="ready",
            model_lifecycle="ready",
            detail="ready",
        )

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc) as popen_mock, \
             patch.object(adapter, "check_health", AsyncMock(return_value=health_ready)):
            import asyncio
            asyncio.run(adapter.warmup())

        popen_mock.assert_called_once()
        assert adapter._managed_process is not None

    def test_warmup_is_idempotent(self):
        """Calling warmup twice with a running process should not launch a duplicate."""
        config = self._make_managed_config()
        adapter = LlamaCppAdapter(config=config)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999

        health_ready = _LlamaCppHealthStatus(
            reachable=True,
            runner_status="ready",
            model_lifecycle="ready",
            detail="ready",
        )

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc) as popen_mock, \
             patch.object(adapter, "check_health", AsyncMock(return_value=health_ready)):
            import asyncio
            asyncio.run(adapter.warmup())
            asyncio.run(adapter.warmup())  # Second call

        # Only one Popen call.
        assert popen_mock.call_count == 1

    def test_warmup_handles_launch_failure(self):
        config = self._make_managed_config()
        adapter = LlamaCppAdapter(config=config)

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", side_effect=OSError("no binary")):
            import asyncio
            with pytest.raises(LlamaCppProcessError, match="Failed to launch"):
                asyncio.run(adapter.warmup())

    async def test_warmup_handles_startup_timeout(self):
        config = self._make_managed_config()
        config.startup_timeout_seconds = 0.05
        adapter = LlamaCppAdapter(config=config)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345

        health_warming = _LlamaCppHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="warmup",
            detail="warming",
        )

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(adapter, "check_health", AsyncMock(return_value=health_warming)):
            with pytest.raises(LlamaCppProcessError, match="did not become ready"):
                await adapter.warmup()


# ── Adapter lifecycle — unload ──────────────────────────────────────────────


class TestAdapterManagedUnload:
    def test_unload_stops_managed_process(self, valid_config):
        adapter = LlamaCppAdapter(config=valid_config)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc):
            adapter._managed_process = ManagedLlamaServer(valid_config)
            adapter._managed_process.start()

        import asyncio
        asyncio.run(adapter.unload())
        mock_proc.terminate.assert_called_once()
        assert adapter._managed_process is None

    def test_unload_safe_when_process_never_started(self, valid_config):
        adapter = LlamaCppAdapter(config=valid_config)
        import asyncio
        asyncio.run(adapter.unload())  # Should not raise

    def test_unload_does_not_stop_external_server(self):
        """unload() should not affect externally managed servers."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                server_url="http://192.168.1.1:8080",
                auto_start=False,
            )
        )
        import asyncio
        asyncio.run(adapter.unload())  # Should not raise
        assert adapter._managed_process is None


# ── is_loaded for managed mode ──────────────────────────────────────────────


class TestAdapterIsLoadedManaged:
    def test_is_loaded_false_when_no_process(self, valid_config):
        adapter = LlamaCppAdapter(config=valid_config)
        assert adapter.is_loaded() is False

    def test_is_loaded_true_when_process_running(self, valid_config):
        adapter = LlamaCppAdapter(config=valid_config)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc):
            adapter._managed_process = ManagedLlamaServer(valid_config)
            adapter._managed_process.start()

        assert adapter.is_loaded() is True

    def test_is_loaded_false_after_process_exits(self, valid_config):
        adapter = LlamaCppAdapter(config=valid_config)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc):
            adapter._managed_process = ManagedLlamaServer(valid_config)
            adapter._managed_process.start()

        mock_proc.poll.return_value = 1  # Exited
        assert adapter.is_loaded() is False


# ── Health state mapping for supervised processes ───────────────────────────


class TestHealthSupervisedProcess:
    def test_health_when_process_not_started(self):
        """auto_start=true but no process → unloaded."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path="/bin/llama-server",
                model_path="/m.gguf",
                auto_start=True,
            )
        )
        import asyncio
        status = asyncio.run(adapter.check_health())
        assert status.reachable is False
        assert status.model_lifecycle == "unloaded"
        assert "not started" in status.detail

    def test_health_when_process_exited(self):
        """Process exited → failed/degraded."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path="/bin/llama-server",
                model_path="/m.gguf",
                auto_start=True,
            )
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Exited with error

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc):
            adapter._managed_process = ManagedLlamaServer(adapter.config)
            adapter._managed_process.start()

        import asyncio
        status = asyncio.run(adapter.check_health())
        assert status.reachable is False
        assert status.model_lifecycle == "failed"
        assert status.runner_status == "degraded"
        assert "exited unexpectedly" in status.detail

    def test_health_when_process_alive_but_not_ready(self):
        """Process alive, health probe returns non-ready → warmup."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path="/bin/llama-server",
                model_path="/m.gguf",
                auto_start=True,
                host="127.0.0.1",
                port=8081,
            )
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        health_warming = _LlamaCppHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="warmup",
            detail="warming",
        )

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(adapter, "_probe_server", AsyncMock(return_value=health_warming)):
            adapter._managed_process = ManagedLlamaServer(adapter.config)
            adapter._managed_process.start()

            import asyncio
            status = asyncio.run(adapter.check_health())
            assert status.model_lifecycle == "warmup"

    def test_health_when_process_alive_and_ready(self):
        """Process alive, health probe returns ready → ready."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                binary_path="/bin/llama-server",
                model_path="/m.gguf",
                auto_start=True,
                host="127.0.0.1",
                port=8082,
            )
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        health_ready = _LlamaCppHealthStatus(
            reachable=True,
            runner_status="ready",
            model_lifecycle="ready",
            detail="ready",
            raw_status=200,
        )

        with patch("os.path.isfile", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(adapter, "_probe_server", AsyncMock(return_value=health_ready)):
            adapter._managed_process = ManagedLlamaServer(adapter.config)
            adapter._managed_process.start()

            import asyncio
            status = asyncio.run(adapter.check_health())
            assert status.reachable is True
            assert status.model_lifecycle == "ready"


# ── External server mode (unchanged behaviour) ──────────────────────────────


class TestExternalServerModeUnchanged:
    async def test_external_server_health_still_works(self):
        """External server mode health probing is unaffected."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                server_url="http://127.0.0.1:8080",
                auto_start=False,
            )
        )
        import httpx
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()
            assert status.reachable is True
            assert status.model_lifecycle == "ready"

    async def test_external_server_warmup_still_probes(self):
        """External server warmup still probes health."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                server_url="http://127.0.0.1:8080",
                auto_start=False,
            )
        )
        with patch.object(adapter, "check_health", AsyncMock()) as mock_probe:
            await adapter.warmup()
            mock_probe.assert_called_once()


# ── Config from env — model_path ───────────────────────────────────────────


class TestConfigModelPathFromEnv:
    def test_model_path_from_env(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_MODEL_PATH", "/models/env-model.gguf")
        from whooshd.adapters.llama_cpp import _build_config_from_env
        config = _build_config_from_env()
        assert config.model_path == "/models/env-model.gguf"

    def test_model_path_none_when_not_set(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_LLAMA_CPP_MODEL_PATH", raising=False)
        from whooshd.adapters.llama_cpp import _build_config_from_env
        config = _build_config_from_env()
        assert config.model_path is None

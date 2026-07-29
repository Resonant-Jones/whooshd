"""llama.cpp adapter with supervised process lifecycle for Whoosh'd.

This adapter defines the GGUF execution lane with two operating modes:

  * **Remote server mode** — probes an existing llama.cpp server via HTTP
  * **Managed mode** (auto_start=true) — Whoosh'd owns the llama-server
    subprocess: validates config, builds safe argv, starts the process,
    probes readiness, and shuts it down cleanly.

Chat completion requests are forwarded to the llama.cpp server's
OpenAI-compatible endpoint (/v1/chat/completions) with SSE streaming
support.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import logging
import os
import signal
from pathlib import Path
import subprocess
import time
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    RequestExecutionContext,
    ResponseRuntimeInfo,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeKind,
    RuntimeModel,
    TokenUsage,
)

# Optional httpx import — only needed when server_url is configured.
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


# ── Adapter configuration ────────────────────────────────────────────────────


class LlamaCppAdapterConfig(BaseModel):
    """Typed configuration for the llama.cpp adapter.

    Every field can be set via environment variable or direct construction.
    """

    server_url: str | None = Field(
        None, description="Base URL of an existing llama.cpp server (e.g. http://127.0.0.1:8080)"
    )
    binary_path: str | None = Field(
        None, description="Path to llama-server binary (for future managed mode)"
    )
    model_path: str | None = Field(
        None, description="Path to the GGUF model file"
    )
    host: str = Field(
        "127.0.0.1", description="Host for a locally-managed server"
    )
    port: int = Field(
        8080, ge=1, le=65535, description="Port for a locally-managed server"
    )
    context_window: int | None = Field(
        None, description="Override context window size"
    )
    parallel_slots: int | None = Field(
        None, description="Number of parallel inference slots"
    )
    extra_args: list[str] = Field(
        default_factory=list, description="Extra CLI args for llama-server"
    )
    startup_timeout_seconds: float = Field(
        30.0, ge=0.1, description="Max seconds to wait for managed server startup"
    )
    health_timeout_seconds: float = Field(
        2.0, ge=0.1, description="Max seconds for a health probe HTTP request"
    )
    auto_start: bool = Field(
        False, description="Whether to auto-start a managed llama.cpp server"
    )


# ── Adapter class ───────────────────────────────────────────────────────────


class _LlamaCppNotImplementedError(NotImplementedError):
    """Intentional not-implemented marker for llama.cpp inference.

    Raised when inference is called before the llama.cpp execution layer
    is wired.  This is a project-specific sentinel, not a generic error.
    """

    def __init__(self, method: str):
        super().__init__(
            f"llama.cpp adapter method '{method}' is not available. "
            "This adapter requires a configured server URL."
        )


class LlamaCppConfigError(Exception):
    """Raised when managed-mode configuration is invalid or incomplete."""


class LlamaCppProcessError(Exception):
    """Raised when a supervised llama-server process operation fails."""


# ── argv builder ────────────────────────────────────────────────────────────


def build_llama_server_argv(
    config: LlamaCppAdapterConfig,
    model_path_override: str | None = None,
) -> list[str]:
    """Build a safe argv list for llama-server.

    Never uses ``shell=True``.  Returns a list of string arguments
    suitable for ``subprocess.Popen``.

    If *model_path_override* is provided, it replaces ``config.model_path``
    in the argv.  This supports external model path binding (Phase 4B).

    Raises ``LlamaCppConfigError`` if required fields are missing or invalid.
    """
    _validate_managed_config(config, model_path_override=model_path_override)

    effective_model = model_path_override or config.model_path

    argv: list[str] = [
        config.binary_path,  # type: ignore[arg-type]  # validated above
        "--model", effective_model,  # type: ignore[arg-type]
        "--host", config.host,
        "--port", str(config.port),
    ]

    if config.context_window is not None and config.context_window > 0:
        argv.extend(["--ctx-size", str(config.context_window)])

    if config.parallel_slots is not None and config.parallel_slots > 0:
        argv.extend(["--parallel", str(config.parallel_slots)])

    if config.extra_args:
        argv.extend(config.extra_args)

    logger.info(
        "llama_cpp.process.argv_built binary=%s model=%s host=%s port=%s",
        config.binary_path,
        config.model_path,
        config.host,
        config.port,
    )
    return argv


# ── Config validation ───────────────────────────────────────────────────────


def _validate_managed_config(
    config: LlamaCppAdapterConfig,
    model_path_override: str | None = None,
) -> None:
    """Validate that the config has everything needed for managed mode.

    Checks field presence, format, and range.  Does NOT check whether
    files exist on disk (that's a pre-flight check before launch).

    If *model_path_override* is provided, validates it instead of
    ``config.model_path`` (external path binding, Phase 4B).

    Raises ``LlamaCppConfigError`` on the first validation failure.
    """
    effective_model = model_path_override or config.model_path

    if not config.binary_path:
        raise LlamaCppConfigError(
            "auto_start=true requires binary_path to be set"
        )

    if not effective_model:
        raise LlamaCppConfigError(
            "auto_start=true requires model_path to be set"
        )

    if not effective_model.endswith(".gguf"):  # type: ignore[union-attr]
        raise LlamaCppConfigError("Model path must end in .gguf")

    if config.port < 1 or config.port > 65535:
        raise LlamaCppConfigError(
            f"Port out of valid range (1-65535): {config.port}"
        )

    if config.context_window is not None and config.context_window <= 0:
        raise LlamaCppConfigError(
            f"context_window must be positive, got {config.context_window}"
        )

    if config.parallel_slots is not None and config.parallel_slots <= 0:
        raise LlamaCppConfigError(
            f"parallel_slots must be positive, got {config.parallel_slots}"
        )


def _validate_files_exist(
    config: LlamaCppAdapterConfig,
    model_path_override: str | None = None,
) -> None:
    """Pre-flight check that binary and model files exist on disk.

    Called before process launch, separate from field-level validation
    so argv tests don't need filesystem mocks.

    If *model_path_override* is provided, checks that file instead of
    ``config.model_path`` (external path binding, Phase 4B).

    Raises ``LlamaCppConfigError`` if either file is missing.
    """
    effective_model = model_path_override or config.model_path

    if not os.path.isfile(config.binary_path):  # type: ignore[arg-type]
        raise LlamaCppConfigError(
            f"llama-server binary not found: {config.binary_path}"
        )

    if not os.path.isfile(effective_model):  # type: ignore[arg-type]
        if model_path_override is not None:
            # Client-safe message for external paths.
            raise LlamaCppConfigError(
                "External model path is unavailable at execution time."
            )
        raise LlamaCppConfigError("Model file not found")


# ── Managed process wrapper ─────────────────────────────────────────────────


class ManagedLlamaServer:
    """Lightweight subprocess wrapper for a locally-managed llama-server.

    Owns the subprocess lifecycle: start, stop, restart, and health-ready
    polling.  All process operations are synchronous; health probing is
    delegated to the caller via an async callback.
    """

    def __init__(self, config: LlamaCppAdapterConfig) -> None:
        self._config = config
        self._process: subprocess.Popen[str] | None = None
        self._started_at: float | None = None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Return True if the subprocess is alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        """Return the PID of the managed process, or None."""
        if self._process is not None:
            return self._process.pid
        return None

    @property
    def returncode(self) -> int | None:
        """Return the subprocess return code, or None if still running."""
        if self._process is not None:
            return self._process.poll()
        return None

    # ── Start / stop ──────────────────────────────────────────────────

    def start(self) -> subprocess.Popen[str]:
        """Build argv and launch llama-server as a subprocess.

        Uses ``shell=False``.  Captures stdout and stderr.

        Returns the ``Popen`` handle.

        Raises ``LlamaCppProcessError`` if the process is already running.
        Raises ``LlamaCppConfigError`` if required config files are missing.
        """
        if self.is_running:
            raise LlamaCppProcessError(
                "llama-server is already running (pid={})".format(self.pid)
            )

        # Field-level validation first, then file-existence.
        _validate_managed_config(self._config)
        _validate_files_exist(self._config)
        argv = build_llama_server_argv(self._config)

        logger.info(
            "llama_cpp.process.starting binary=%s model=%s port=%s",
            self._config.binary_path,
            self._config.model_path,
            self._config.port,
        )

        try:
            self._process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise LlamaCppProcessError(
                f"Failed to launch llama-server: {exc}"
            ) from exc

        self._started_at = time.monotonic()

        logger.info(
            "llama_cpp.process.started pid=%s",
            getattr(self._process, "pid", None),
        )
        return self._process

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the managed process.

        Escalation: graceful SIGTERM first, then SIGKILL after *timeout*
        seconds if the process hasn't exited.

        Safe to call when no process is running.
        """
        if self._process is None:
            return

        if self._process.poll() is not None:
            # Already exited.
            self._process = None
            self._started_at = None
            return

        _pid = getattr(self._process, "pid", None)
        logger.info("llama_cpp.process.stopping pid=%s", _pid)

        # Graceful terminate.
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "llama_cpp.process.force_kill pid=%s timeout=%ss",
                _pid,
                timeout,
            )
            self._process.kill()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.error(
                    "llama_cpp.process.kill_failed pid=%s",
                    _pid,
                )

        logger.info(
            "llama_cpp.process.stopped pid=%s returncode=%s",
            _pid,
            getattr(self._process, "returncode", None),
        )
        self._process = None
        self._started_at = None

    def restart(self) -> subprocess.Popen[str]:
        """Stop (if running) and start the process."""
        self.stop()
        return self.start()

    # ── Readiness polling ─────────────────────────────────────────────

    async def wait_until_ready(
        self,
        health_fn,
        startup_timeout: float,
        probe_interval: float = 0.25,
    ) -> None:
        """Poll *health_fn* until it reports reachable.

        *health_fn* must be an async callable that returns a
        ``_LlamaCppHealthStatus``.

        Raises ``LlamaCppProcessError`` if the process exits before
        becoming ready, or if the startup timeout expires.
        """
        deadline = time.monotonic() + startup_timeout

        while time.monotonic() < deadline:
            # Check process liveness first.
            if not self.is_running:
                rc = self.returncode
                raise LlamaCppProcessError(
                    f"llama-server exited unexpectedly with code {rc} "
                    f"before becoming ready"
                )

            status = await health_fn()
            logger.debug(
                "llama_cpp.process.health_probe reachable=%s lifecycle=%s",
                status.reachable,
                status.model_lifecycle,
            )

            if status.reachable:
                logger.info("llama_cpp.process.ready pid=%s", getattr(self._process, "pid", None) if self._process else None)
                return

            await asyncio.sleep(probe_interval)

        # Timeout — process may still be running.
        logger.error(
            "llama_cpp.process.startup_timeout pid=%s timeout=%ss",
            self.pid,
            startup_timeout,
        )
        self.stop()
        raise LlamaCppProcessError(
            f"llama-server did not become ready within {startup_timeout}s"
        )

    def check_exited(self) -> bool:
        """Return True if the process has exited (crashed).

        If the process exited, log it and clear the handle so future
        calls report unloaded rather than falsely alive.
        """
        if self._process is None:
            return False
        rc = self._process.poll()
        if rc is not None:
            logger.warning(
                "llama_cpp.process.exited pid=%s returncode=%s",
                self._process.pid,
                rc,
            )
            self._process = None
            self._started_at = None
            return True
        return False


# ── Adapter class ───────────────────────────────────────────────────────────


class LlamaCppAdapter:
    """llama.cpp inference adapter with optional process supervision.

    Two modes:

    * **External server** — ``server_url`` is set, ``auto_start=false``.
      Whoosh'd probes the external server for health but does not manage
      its lifecycle.

    * **Managed server** — ``auto_start=true``.  Whoosh'd validates config,
      builds safe argv, launches ``llama-server`` as a subprocess, probes
      readiness, and shuts it down on unload.

    Chat completion requests are forwarded to the server's
    /v1/chat/completions endpoint.
    """

    def __init__(self, config: LlamaCppAdapterConfig | None = None) -> None:
        self._config = config or _build_config_from_env()
        self._managed_process: ManagedLlamaServer | None = None
        self._external_model_path_var: ContextVar[Path | None] = ContextVar(
            f"llama_cpp_external_model_path_{id(self)}", default=None
        )

        # Per-runtime concurrency guard.
        from whooshd.config import get_llama_cpp_max_concurrent_requests
        import asyncio as _asyncio
        self._max_concurrent = get_llama_cpp_max_concurrent_requests()
        self._concurrency_semaphore = _asyncio.Semaphore(self._max_concurrent)

    # ── External path binding (Phase 4B) ─────────────────────────────

    def set_external_model_path(self, path: str) -> None:
        """Bind an external model path for the next request.

        Called by the router before dispatching an external model request.
        The path is used exactly as-is — no copying, registration, or
        normalization.
        """
        self._external_model_path_var.set(Path(path))

    @property
    def _external_model_path(self) -> Path | None:
        """Return the external binding for this async request context."""
        return self._external_model_path_var.get()

    def _clear_external_model_path(self) -> None:
        """Clear external path state after a request completes."""
        self._external_model_path_var.set(None)

    @property
    def _effective_model_path(self) -> str | None:
        """Return the active model path: external if set, otherwise config."""
        if self._external_model_path is not None:
            return str(self._external_model_path)
        return self._config.model_path

    def _reject_managed_external_binding(self) -> None:
        """Avoid forwarding to a managed server loaded with another model."""
        if self._config.auto_start and self._external_model_path is not None:
            from whooshd.http_forwarding import RuntimeUnavailable

            raise RuntimeUnavailable(
                "External models cannot use a managed llama.cpp runtime."
            )

    # ── Protocol properties ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "llama-cpp"

    @property
    def kind(self) -> str:
        return RuntimeKind.LLAMA_CPP.value

    @property
    def supports_streaming(self) -> bool:
        # llama.cpp server supports SSE streaming natively.
        return True

    # ── Configuration accessors ────────────────────────────────────────

    @property
    def config(self) -> LlamaCppAdapterConfig:
        return self._config

    @property
    def _server_url(self) -> str | None:
        """Resolved server URL.

        Priority:
          1. Explicit ``server_url`` in config (always used for external mode).
          2. When ``auto_start=true`` and no explicit ``server_url``, derive
             from ``host`` and ``port``.
          3. Otherwise ``None``.
        """
        url = self._config.server_url
        if url:
            return url.rstrip("/")
        if self._config.auto_start:
            return self._build_owned_server_url()
        return None

    def _build_owned_server_url(self) -> str:
        """Build the server URL from host and port for managed mode."""
        return f"http://{self._config.host}:{self._config.port}"

    def _validate_server_url(self) -> None:
        """Raise ValueError if server_url is set but malformed."""
        url = self._config.server_url
        if url is not None:
            url = url.rstrip("/")
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(
                    f"Invalid llama.cpp server_url: '{url}' — "
                    "must include scheme and host (e.g. http://127.0.0.1:8080)"
                )

    # ── Health probing ──────────────────────────────────────────────────

    async def check_health(self) -> _LlamaCppHealthStatus:
        """Probe the llama.cpp server health.

        Three modes:
          * **Managed mode** (auto_start=true) — checks supervised process
            liveness, then probes the derived server URL.
          * **Remote server mode** — probes the configured ``server_url``.
          * **Config-only mode** — no server, returns unloaded.

        Returns a ``_LlamaCppHealthStatus`` describing reachability and
        the mapped Whoosh'd states.
        """
        # ── Managed mode: account for supervised process state ─────
        if self._config.auto_start:
            proc = self._managed_process

            if proc is None:
                return _LlamaCppHealthStatus(
                    reachable=False,
                    runner_status="ready",
                    model_lifecycle="unloaded",
                    detail="llama-server process not started",
                )

            # Check if the process crashed.
            proc.check_exited()

            if not proc.is_running:
                return _LlamaCppHealthStatus(
                    reachable=False,
                    runner_status="degraded",
                    model_lifecycle="failed",
                    detail="llama-server process exited unexpectedly",
                )

            # Process is alive — probe it.
            url = self._server_url
            if url is None:
                return _LlamaCppHealthStatus(
                    reachable=False,
                    runner_status="ready",
                    model_lifecycle="unloaded",
                    detail="llama-server process is running but no server URL configured",
                )
            return await self._probe_server(url)

        # ── External / config-only ─────────────────────────────────
        url = self._server_url
        if url is None:
            return _LlamaCppHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="unloaded",
                detail="No server_url configured and auto_start disabled; llama.cpp server is not reachable.",
            )

        self._validate_server_url()
        return await self._probe_server(url)

    async def _probe_server(self, url: str) -> _LlamaCppHealthStatus:
        """Core HTTP health probe against a specific URL."""

        if httpx is None:
            return _LlamaCppHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="unloaded",
                detail="httpx is not installed; cannot probe llama.cpp server.",
            )

        timeout = self._config.health_timeout_seconds

        # Probe /health first (preferred).
        # Catch exceptions broadly — in tests httpx may be mocked.
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    return _LlamaCppHealthStatus(
                        reachable=True,
                        runner_status="ready",
                        model_lifecycle="ready",
                        detail="llama.cpp server /health returned 200.",
                        raw_status=resp.status_code,
                    )
                # Non-200 from /health → try /v1/models as fallback.
        except Exception as exc:
            return _classify_health_exception(exc, timeout)

        # Fallback: try /v1/models.
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}/v1/models")
                if resp.status_code == 200:
                    return _LlamaCppHealthStatus(
                        reachable=True,
                        runner_status="ready",
                        model_lifecycle="ready",
                        detail="llama.cpp server /v1/models returned 200.",
                        raw_status=resp.status_code,
                    )
                else:
                    return _LlamaCppHealthStatus(
                        reachable=True,
                        runner_status="ready",
                        model_lifecycle="warmup",
                        detail=f"llama.cpp server reachable but /v1/models returned {resp.status_code}.",
                        raw_status=resp.status_code,
                    )
        except Exception as exc:
            return _classify_health_exception(exc, timeout)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def is_loaded(self) -> bool:
        """Return True if a managed server is running and reachable.

        For external server mode, returns False (synchronous probe avoided).
        For managed mode, checks that the supervised process is alive.
        """
        if self._config.auto_start and self._managed_process is not None:
            self._managed_process.check_exited()
            return self._managed_process.is_running
        return False

    def model_id(self) -> Optional[str]:
        """Return the active model path (external if set, otherwise config)."""
        return self._effective_model_path

    async def warmup(self) -> None:
        """Start the managed process (if auto_start) or probe external server.

        Managed mode:
          1. Validates config (binary_path, model_path, etc.).
          2. Creates a ``ManagedLlamaServer`` if one does not exist.
          3. If the process is not already running, starts it.
          4. Polls health until reachable or startup timeout.
          Repeated calls are idempotent.

        External server mode:
          Probes the configured server_url via health check.
        """
        if self._config.auto_start:
            # Create the process wrapper if needed.
            if self._managed_process is None:
                self._managed_process = ManagedLlamaServer(self._config)

            proc = self._managed_process
            proc.check_exited()

            if not proc.is_running:
                proc.start()

                # Wait for readiness.
                await proc.wait_until_ready(
                    health_fn=self.check_health,
                    startup_timeout=self._config.startup_timeout_seconds,
                )
            else:
                # Already running — just verify health.
                await self.check_health()
            return

        # External server mode.
        if self._server_url is not None:
            try:
                await self.check_health()
            except Exception:
                pass

    async def unload(self) -> None:
        """Stop the managed process if Whoosh'd owns it.

        Does nothing for external server mode.
        Safe to call repeatedly.
        """
        if self._managed_process is not None:
            self._managed_process.stop()
            self._managed_process = None

    # ── Multi-runtime introspection ──────────────────────────────────

    async def health(self) -> RuntimeHealth:
        """Return the current health state of this llama.cpp runtime.

        Delegates to ``check_health()`` and maps its result to
        a ``RuntimeHealth`` model.
        """
        status = await self.check_health()

        # Map the string lifecycle/model_lifecycle to RuntimeHealthState.
        state_map: dict[str, RuntimeHealthState] = {
            "ready": RuntimeHealthState.READY,
            "warmup": RuntimeHealthState.MODEL_WARMING,
            "unloaded": RuntimeHealthState.OFFLINE,
            "failed": RuntimeHealthState.ERROR,
            "degraded": RuntimeHealthState.DEGRADED,
        }
        state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE)

        enabled = bool(self._config.server_url or self._config.auto_start)

        return RuntimeHealth(
            kind=self.kind,
            enabled=enabled,
            state=state,
            active_model=self._effective_model_path if self.is_loaded() else None,
            detail=status.detail,
        )

    async def list_models(self) -> list[RuntimeModel]:
        """Return models managed by this llama.cpp runtime."""
        if not self._effective_model_path:
            return []

        loaded = self.is_loaded()
        status = await self.check_health()
        state_map: dict[str, str] = {
            "ready": RuntimeHealthState.READY.value,
            "warmup": RuntimeHealthState.MODEL_WARMING.value,
            "unloaded": RuntimeHealthState.OFFLINE.value,
            "failed": RuntimeHealthState.ERROR.value,
            "degraded": RuntimeHealthState.DEGRADED.value,
        }
        model_state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE.value)

        model_id = self._effective_model_path
        display_name = os.path.basename(model_id) if model_id else model_id

        return [
            RuntimeModel(
                id=model_id,
                display_name=display_name or "",
                runtime=self.kind,
                format="gguf",
                path=model_id,
                context_window=self._config.context_window,
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                loaded=loaded,
                state=model_state,
            )
        ]

    # ── Inference ────────────────────────────────────────────────────

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Generate with request-scoped external binding cleanup."""
        try:
            self._reject_managed_external_binding()
            return await self._generate_bound(request)
        finally:
            self._clear_external_model_path()

    async def _generate_bound(self, request: GenerateRequest) -> GenerateResponse:
        """Codexify-style generate — forwarded to llama.cpp server.

        Converts the prompt to a single-message chat completion,
        then maps the response back to GenerateResponse format.
        llama.cpp servers typically do not have a dedicated /v1/generate
        endpoint, so this is a compatibility bridge.
        """
        from whooshd.http_forwarding import (
            RuntimeUnavailable,
            RuntimeWarming,
            forward_non_streaming,
        )

        url = self._server_url
        if url is None:
            raise RuntimeUnavailable(
                "llama.cpp server is not configured or not reachable."
            )

        # Verify the server is ready before forwarding.
        health = await self.check_health()
        if not health.reachable:
            if health.model_lifecycle == "warming":
                raise RuntimeWarming(
                    "llama.cpp server is reachable but model is still warming."
                )
            raise RuntimeUnavailable(
                f"llama.cpp server is not ready: {health.detail}"
            )

        import time, uuid

        # Convert generate request to chat completion.
        chat_req = ChatCompletionRequest(
            model=request.model_id or (self._effective_model_path or "gguf-model"),
            messages=[ChatMessage(role="user", content=request.prompt)],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
        )

        chat_resp = await forward_non_streaming(
            url, chat_req, timeout=300.0,
            model_override=self._effective_model_path,
            adapter_kind=self.kind,
        )

        # Map ChatCompletionResponse → GenerateResponse.
        content = ""
        if chat_resp.choices:
            content = chat_resp.choices[0].message.content

        prompt_tokens = chat_resp.usage.prompt_tokens if chat_resp.usage else None
        completion_tokens = chat_resp.usage.completion_tokens if chat_resp.usage else None
        total_tokens = chat_resp.usage.total_tokens if chat_resp.usage else None

        return GenerateResponse(
            ok=True,
            request_id=request.request_id or str(uuid.uuid4()),
            model_id=chat_resp.model,
            text=content,
            finish_reason=chat_resp.choices[0].finish_reason if chat_resp.choices else "stop",
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            runtime=ResponseRuntimeInfo(
                adapter=self.name,
                queued=False,
                elapsed_ms=0.0,
            ),
        )

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        context: RequestExecutionContext | None = None,
    ) -> ChatCompletionResponse:
        """Forward a non-streaming chat completion to the llama.cpp server.

        Acquires a concurrency slot; rejects with 429 if at capacity.
        """
        from whooshd.http_forwarding import (
            RuntimeOverloaded,
            RuntimeUnavailable,
            RuntimeWarming,
            forward_non_streaming,
        )
        from whooshd.config import get_runtime_acquire_timeout_seconds

        url = self._server_url
        if url is None:
            raise RuntimeUnavailable(
                "llama.cpp server is not configured or not reachable."
            )

        # Acquire concurrency slot.
        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(
                f"llama.cpp runtime at capacity ({self._max_concurrent} concurrent requests). "
                f"Try again later."
            )

        try:
            self._reject_managed_external_binding()
            # Verify the server is ready for inference before forwarding.
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming(
                        "llama.cpp server is reachable but model is still warming."
                    )
                raise RuntimeUnavailable(
                    f"llama.cpp server is not ready: {health.detail}"
                )

            return await forward_non_streaming(
                url, request, timeout=300.0,
                model_override=self._effective_model_path,
                adapter_kind=self.kind,
            )
        finally:
            self._concurrency_semaphore.release()
            self._clear_external_model_path()

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        context: RequestExecutionContext | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Forward a streaming chat completion to the llama.cpp server.

        Acquires a concurrency slot; releases it on completion/error/disconnect.
        """
        from whooshd.http_forwarding import (
            RuntimeOverloaded,
            RuntimeUnavailable,
            RuntimeWarming,
            forward_streaming,
        )
        from whooshd.config import get_runtime_acquire_timeout_seconds

        url = self._server_url
        if url is None:
            raise RuntimeUnavailable(
                "llama.cpp server is not configured or not reachable."
            )

        # Acquire concurrency slot.
        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(
                f"llama.cpp runtime at capacity ({self._max_concurrent} concurrent requests). "
                f"Try again later."
            )

        try:
            self._reject_managed_external_binding()
            # Verify the server is ready for inference before forwarding.
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming(
                        "llama.cpp server is reachable but model is still warming."
                    )
                raise RuntimeUnavailable(
                    f"llama.cpp server is not ready: {health.detail}"
                )

            cancellation_token = context.cancellation_token if context else None
            async for chunk in forward_streaming(
                url, request, timeout=300.0,
                model_override=self._effective_model_path,
                adapter_kind=self.kind,
                cancellation_token=cancellation_token,
            ):
                yield chunk
        finally:
            self._concurrency_semaphore.release()
            self._clear_external_model_path()


# ── Request normalization ───────────────────────────────────────────────────


def normalize_chat_request_for_llama_cpp(request: ChatCompletionRequest) -> dict:
    """Convert a Whoosh'd/OpenAI-compatible chat request into a dict
    suitable for the llama.cpp server /v1/chat/completions endpoint.

    This is a convenience utility; the main forwarding path uses
    ``build_forward_body`` from ``whooshd.http_forwarding``.
    """
    # Build a dict matching the llama.cpp server /v1/chat/completions shape.
    return {
        "model": request.model,
        "messages": [
            {"role": m.role, "content": m.content} for m in request.messages
        ],
        "stream": request.stream,
    }


# ── Health status record ───────────────────────────────────────────────────


class _LlamaCppHealthStatus:
    """Result of a llama.cpp server health probe.

    Maps probe outcomes to Whoosh'd runner_status and model_lifecycle
    values, plus a human-readable detail string.
    """

    def __init__(
        self,
        *,
        reachable: bool,
        runner_status: str,
        model_lifecycle: str,
        detail: str,
        raw_status: int | None = None,
    ) -> None:
        self.reachable = reachable
        self.runner_status = runner_status
        self.model_lifecycle = model_lifecycle
        self.detail = detail
        self.raw_status = raw_status

    def __repr__(self) -> str:
        return (
            f"_LlamaCppHealthStatus(reachable={self.reachable!r}, "
            f"runner_status={self.runner_status!r}, "
            f"model_lifecycle={self.model_lifecycle!r}, "
            f"detail={self.detail!r})"
        )


# ── Internal helpers ────────────────────────────────────────────────────────


def _classify_health_exception(exc: Exception, timeout: float) -> _LlamaCppHealthStatus:
    """Classify a health probe exception by type name and message.

    Uses string-based classification rather than typed except clauses
    so that tests can safely mock httpx without type errors.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    if "connecterror" in exc_name.lower() or "connection" in exc_msg:
        return _LlamaCppHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="unloaded",
            detail="Connection refused — llama.cpp server appears offline.",
        )

    if "timeout" in exc_name.lower() or "timeout" in exc_msg:
        return _LlamaCppHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="unloaded",
            detail=f"Health probe timed out after {timeout}s.",
        )

    return _LlamaCppHealthStatus(
        reachable=False,
        runner_status="degraded",
        model_lifecycle="failed",
        detail=f"Unexpected health probe error: {exc}",
    )


def _build_config_from_env() -> LlamaCppAdapterConfig:
    """Build a LlamaCppAdapterConfig from environment variables."""
    from whooshd.config import (
        get_llama_cpp_auto_start,
        get_llama_cpp_binary_path,
        get_llama_cpp_health_timeout_seconds,
        get_llama_cpp_host,
        get_llama_cpp_model_path,
        get_llama_cpp_port,
        get_llama_cpp_server_url,
        get_llama_cpp_startup_timeout_seconds,
    )

    return LlamaCppAdapterConfig(
        server_url=get_llama_cpp_server_url() or None,
        binary_path=get_llama_cpp_binary_path() or None,
        model_path=get_llama_cpp_model_path() or None,
        host=get_llama_cpp_host(),
        port=get_llama_cpp_port(),
        auto_start=get_llama_cpp_auto_start(),
        startup_timeout_seconds=get_llama_cpp_startup_timeout_seconds(),
        health_timeout_seconds=get_llama_cpp_health_timeout_seconds(),
    )


# ── Concurrency guard helper ──────────────────────────────────────────────


async def _acquire_slot(semaphore, timeout: float) -> bool:
    """Try to acquire a semaphore slot within *timeout* seconds.

    Returns True if acquired, False if timed out.
    """
    import asyncio as _asyncio
    try:
        await _asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        return True
    except _asyncio.TimeoutError:
        return False

"""MLX-LM Server adapter with supervised process lifecycle for Whoosh'd.

This adapter manages ``mlx_lm.server`` as a subprocess backend,
parallel to how the llama.cpp adapter manages ``llama-server``.

Whoosh'd owns:
  * subprocess launch, readiness polling, and shutdown
  * health state classification (startup, warming, ready, etc.)
  * model discovery via proxy to the server's /v1/models endpoint
  * chat completion forwarding via /v1/chat/completions

Inference is forwarded to the supervised mlx_lm.server process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import AsyncIterator, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from whooshd.config import (
    get_mlx_lm_server_enabled,
    get_mlx_lm_server_extra_args,
    get_mlx_lm_server_health_timeout_seconds,
    get_mlx_lm_server_host,
    get_mlx_lm_server_model,
    get_mlx_lm_server_port,
    get_mlx_lm_server_startup_timeout_seconds,
)
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

# Optional httpx import — only needed when the server is running.
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


# ── Adapter configuration ────────────────────────────────────────────────────


class MlxLmServerConfig(BaseModel):
    """Typed configuration for the MLX-LM Server adapter.

    Every field can be set via environment variable or direct construction.
    """

    enabled: bool = Field(False, description="Whether this runtime lane is enabled")
    host: str = Field("127.0.0.1", description="Host to bind")
    port: int = Field(8081, ge=1, le=65535, description="Port to bind")
    model: str | None = Field(None, description="HF repo id or local model path")
    extra_args: list[str] = Field(
        default_factory=list, description="Extra CLI args for mlx_lm.server"
    )
    startup_timeout_seconds: float = Field(
        30.0, ge=0.1, description="Max seconds to wait for server startup"
    )
    health_timeout_seconds: float = Field(
        2.0, ge=0.1, description="Max seconds for a health probe HTTP request"
    )

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ── Error types ──────────────────────────────────────────────────────────────


class MlxLmServerConfigError(Exception):
    """Raised when MLX-LM Server configuration is invalid."""


class MlxLmServerProcessError(Exception):
    """Raised when a supervised mlx_lm.server process operation fails."""


class _MlxLmServerNotImplementedError(NotImplementedError):
    """Intentional not-implemented marker for MLX-LM Server inference."""

    def __init__(self, method: str):
        super().__init__(
            f"mlx_lm.server adapter inference ({method}) is not implemented yet. "
            "This phase establishes the supervision lane."
        )


# ── argv builder ────────────────────────────────────────────────────────────


def build_mlx_lm_server_argv(config: MlxLmServerConfig) -> list[str]:
    """Build a safe argv list for ``mlx_lm.server``.

    Never uses ``shell=True``.  Returns a list of string arguments
    suitable for ``subprocess.Popen``.

    Uses ``python -m mlx_lm server`` as the invocation prefix so
    the current Python environment is used, matching the user's
    installed mlx-lm package (mlx-lm >= 0.31 uses the space form).

    Raises ``MlxLmServerConfigError`` if required fields are missing.
    """
    if not config.model:
        raise MlxLmServerConfigError(
            "A model path is required (set WHOOSHD_MLX_MODEL)"
        )

    argv: list[str] = [
        "python", "-m", "mlx_lm", "server",
        "--model", config.model,
        "--host", config.host,
        "--port", str(config.port),
    ]

    if config.extra_args:
        argv.extend(config.extra_args)

    logger.info(
        "mlx_lm_server.process.argv_built model=%s host=%s port=%s",
        config.model,
        config.host,
        config.port,
    )
    return argv


# ── Managed process wrapper ─────────────────────────────────────────────────


class ManagedMlxLmServer:
    """Lightweight subprocess wrapper for a locally-managed mlx_lm.server.

    Owns the subprocess lifecycle: start, stop, restart, and health-ready
    polling.  All process operations are synchronous; health probing is
    delegated to the caller via an async callback.
    """

    def __init__(self, config: MlxLmServerConfig) -> None:
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
        """Build argv and launch mlx_lm.server as a subprocess.

        Uses ``shell=False``.  Captures stdout and stderr.

        Returns the ``Popen`` handle.

        Raises ``MlxLmServerProcessError`` if the process is already running.
        Raises ``MlxLmServerConfigError`` if required config is missing.
        """
        if self.is_running:
            raise MlxLmServerProcessError(
                "mlx_lm.server is already running (pid={})".format(self.pid)
            )

        argv = build_mlx_lm_server_argv(self._config)

        logger.info(
            "mlx_lm_server.process.starting model=%s port=%s",
            self._config.model,
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
            raise MlxLmServerProcessError(
                f"Failed to launch mlx_lm.server: {exc}"
            ) from exc

        self._started_at = time.monotonic()

        logger.info(
            "mlx_lm_server.process.started pid=%s",
            getattr(self._process, "pid", None),
        )
        return self._process

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the managed process.

        Escalation: graceful SIGTERM first, then SIGKILL after *timeout*
        seconds if the process hasn't exited.  MLX models may need more
        time to unload, hence the slightly longer default timeout vs
        llama.cpp.

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
        logger.info("mlx_lm_server.process.stopping pid=%s", _pid)

        # Graceful terminate.
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "mlx_lm_server.process.force_kill pid=%s timeout=%ss",
                _pid,
                timeout,
            )
            self._process.kill()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.error(
                    "mlx_lm_server.process.kill_failed pid=%s",
                    _pid,
                )

        logger.info(
            "mlx_lm_server.process.stopped pid=%s returncode=%s",
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
        probe_interval: float = 0.5,
    ) -> None:
        """Poll *health_fn* until it reports reachable.

        *health_fn* must be an async callable that returns a
        ``_MlxLmServerHealthStatus``.

        Raises ``MlxLmServerProcessError`` if the process exits before
        becoming ready, or if the startup timeout expires.
        """
        deadline = time.monotonic() + startup_timeout

        while time.monotonic() < deadline:
            # Check process liveness first.
            if not self.is_running:
                rc = self.returncode
                raise MlxLmServerProcessError(
                    f"mlx_lm.server exited unexpectedly with code {rc} "
                    f"before becoming ready"
                )

            status = await health_fn()
            logger.debug(
                "mlx_lm_server.process.health_probe reachable=%s lifecycle=%s",
                status.reachable,
                status.model_lifecycle,
            )

            if status.reachable:
                logger.info(
                    "mlx_lm_server.process.ready pid=%s",
                    getattr(self._process, "pid", None) if self._process else None,
                )
                return

            await asyncio.sleep(probe_interval)

        # Timeout — process may still be running.
        logger.error(
            "mlx_lm_server.process.startup_timeout pid=%s timeout=%ss",
            self.pid,
            startup_timeout,
        )
        self.stop()
        raise MlxLmServerProcessError(
            f"mlx_lm.server did not become ready within {startup_timeout}s"
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
                "mlx_lm_server.process.exited pid=%s returncode=%s",
                self._process.pid,
                rc,
            )
            self._process = None
            self._started_at = None
            return True
        return False


# ── Adapter class ───────────────────────────────────────────────────────────


class MlxLmServerAdapter:
    """MLX-LM Server inference adapter with process supervision.

    Whoosh'd owns the mlx_lm.server subprocess:
      * launch via ``python -m mlx_lm server``
      * readiness polling via HTTP health probes
      * clean shutdown on unload
      * chat completion forwarding via /v1/chat/completions
    """

    def __init__(self, config: MlxLmServerConfig | None = None) -> None:
        self._config = config or _build_config_from_env()
        self._managed_process: ManagedMlxLmServer | None = None

        # Per-runtime concurrency guard.
        from whooshd.config import get_mlx_max_concurrent_requests
        import asyncio as _asyncio
        self._max_concurrent = get_mlx_max_concurrent_requests()
        self._concurrency_semaphore = _asyncio.Semaphore(self._max_concurrent)

    # ── Protocol properties ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "mlx-lm-server"

    @property
    def kind(self) -> str:
        return RuntimeKind.MLX_LM_SERVER.value

    @property
    def supports_streaming(self) -> bool:
        # mlx_lm.server supports SSE streaming natively.
        return True

    # ── Configuration accessors ────────────────────────────────────────

    @property
    def config(self) -> MlxLmServerConfig:
        return self._config

    @property
    def _server_url(self) -> str:
        return self._config.server_url

    # ── Health probing ──────────────────────────────────────────────────

    async def check_health(self) -> _MlxLmServerHealthStatus:
        """Probe the mlx_lm.server health.

        Three modes:
          * **Process managed** — checks supervised process liveness,
            then probes the server URL.
          * **External server** — when no managed process exists, probes
            the configured server URL directly.
          * **Not enabled / no model** — returns offline.

        Returns a ``_MlxLmServerHealthStatus`` describing reachability and
        the mapped Whoosh'd states.
        """
        if not self._config.enabled:
            return _MlxLmServerHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="unloaded",
                detail="MLX-LM Server runtime is not enabled (set WHOOSHD_MLX_ENABLED=true).",
            )

        if not self._config.model:
            return _MlxLmServerHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="unloaded",
                detail="No model configured for MLX-LM Server (set WHOOSHD_MLX_MODEL).",
            )

        # Check managed process state.
        proc = self._managed_process

        if proc is not None:
            # Managed mode — check process liveness first.
            proc.check_exited()
            if not proc.is_running:
                return _MlxLmServerHealthStatus(
                    reachable=False,
                    runner_status="degraded",
                    model_lifecycle="failed",
                    detail="mlx_lm.server process exited unexpectedly.",
                )
            return await self._probe_server(self._server_url)

        # External server mode — probe the URL directly.
        return await self._probe_server(self._server_url)

    async def _probe_server(self, url: str) -> _MlxLmServerHealthStatus:
        """Core HTTP health probe against a specific server URL."""

        if httpx is None:
            return _MlxLmServerHealthStatus(
                reachable=False,
                runner_status="ready",
                model_lifecycle="unloaded",
                detail="httpx is not installed; cannot probe mlx_lm.server.",
            )

        timeout = self._config.health_timeout_seconds

        # Probe /v1/models — mlx_lm.server exposes this endpoint.
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}/v1/models")
                if resp.status_code == 200:
                    return _MlxLmServerHealthStatus(
                        reachable=True,
                        runner_status="ready",
                        model_lifecycle="ready",
                        detail="mlx_lm.server /v1/models returned 200.",
                        raw_status=resp.status_code,
                        models_data=resp.json(),
                    )
                # Non-200 — server is reachable but not fully ready.
                return _MlxLmServerHealthStatus(
                    reachable=True,
                    runner_status="ready",
                    model_lifecycle="warming",
                    detail=f"mlx_lm.server reachable but /v1/models returned {resp.status_code}.",
                    raw_status=resp.status_code,
                )
        except Exception as exc:
            return _classify_health_exception(exc, timeout)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def is_loaded(self) -> bool:
        """Return True if the server is confirmed running.

        For managed mode: checks the supervised process.
        For external mode: returns False (callers should use check_health()).
        """
        if self._managed_process is not None:
            self._managed_process.check_exited()
            return self._managed_process.is_running
        return False

    def model_id(self) -> Optional[str]:
        """Return the configured model path, or None."""
        return self._config.model

    async def warmup(self) -> None:
        """Ensure the mlx_lm.server is reachable.

        In managed mode: starts the supervised process if needed.
        In external mode: probes the existing server for readiness.
        Repeated calls are idempotent.
        """
        if not self._config.enabled:
            raise MlxLmServerConfigError(
                "MLX-LM Server runtime is not enabled (set WHOOSHD_MLX_ENABLED=true)."
            )

        if not self._config.model:
            raise MlxLmServerConfigError(
                "No model configured for MLX-LM Server (set WHOOSHD_MLX_MODEL)."
            )

        # External server mode — just verify health.
        if self._managed_process is None:
            status = await self.check_health()
            if not status.reachable:
                raise MlxLmServerProcessError(
                    f"mlx_lm.server is not reachable: {status.detail}"
                )
            return

        # Managed mode — start process if needed.
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

    async def unload(self) -> None:
        """Stop the managed process.

        Safe to call repeatedly.
        """
        if self._managed_process is not None:
            self._managed_process.stop()
            self._managed_process = None

    # ── Multi-runtime introspection ──────────────────────────────────

    async def health(self) -> RuntimeHealth:
        """Return the current health state of this MLX-LM Server runtime."""
        if not self._config.enabled:
            return RuntimeHealth(
                kind=self.kind,
                enabled=False,
                state=RuntimeHealthState.OFFLINE,
                active_model=None,
                detail="MLX-LM Server runtime is disabled.",
            )

        status = await self.check_health()

        state_map: dict[str, RuntimeHealthState] = {
            "ready": RuntimeHealthState.READY,
            "warming": RuntimeHealthState.MODEL_WARMING,
            "unloaded": RuntimeHealthState.OFFLINE,
            "failed": RuntimeHealthState.ERROR,
            "degraded": RuntimeHealthState.DEGRADED,
        }
        state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE)

        return RuntimeHealth(
            kind=self.kind,
            enabled=True,
            state=state,
            active_model=self._config.model if self.is_loaded() else None,
            detail=status.detail,
        )

    async def list_models(self) -> list[RuntimeModel]:
        """Return models managed by this MLX-LM Server runtime."""
        if not self._config.model:
            return []

        loaded = self.is_loaded()
        status = await self.check_health()
        state_map: dict[str, str] = {
            "ready": RuntimeHealthState.READY.value,
            "warming": RuntimeHealthState.MODEL_WARMING.value,
            "unloaded": RuntimeHealthState.OFFLINE.value,
            "failed": RuntimeHealthState.ERROR.value,
            "degraded": RuntimeHealthState.DEGRADED.value,
        }
        model_state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE.value)

        model_id = self._config.model
        display_name = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id

        return [
            RuntimeModel(
                id=model_id,
                display_name=display_name,
                runtime=self.kind,
                format="mlx",
                path=model_id,
                context_window=None,
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                loaded=loaded,
                state=model_state,
            )
        ]

    # ── Inference ────────────────────────────────────────────────────

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Codexify-style generate — forwarded to mlx_lm.server."""
        from whooshd.http_forwarding import (
            RuntimeUnavailable,
            RuntimeWarming,
            forward_non_streaming,
        )

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable(
                "MLX-LM Server runtime is not enabled or no model configured."
            )

        # Verify the server is ready before forwarding.
        health = await self.check_health()
        if not health.reachable:
            if health.model_lifecycle == "warming":
                raise RuntimeWarming(
                    "mlx_lm.server is reachable but model is still warming."
                )
            raise RuntimeUnavailable(
                f"mlx_lm.server is not ready: {health.detail}"
            )

        # Convert generate request to chat completion.
        chat_req = ChatCompletionRequest(
            model=self._config.model,
            messages=[ChatMessage(role="user", content=request.prompt)],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=False,
        )

        import time, uuid

        chat_resp = await forward_non_streaming(
            self._server_url, chat_req, timeout=300.0,
            model_override=self._config.model,
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
        """Forward a non-streaming chat completion to mlx_lm.server.

        Acquires a concurrency slot; rejects with 429 if the runtime
        is at capacity.
        """
        from whooshd.http_forwarding import (
            RuntimeOverloaded,
            RuntimeUnavailable,
            RuntimeWarming,
            forward_non_streaming,
        )
        from whooshd.config import get_runtime_acquire_timeout_seconds

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable(
                "MLX-LM Server runtime is not enabled or no model configured."
            )

        # Acquire concurrency slot.
        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(
                f"MLX-LM Server runtime at capacity ({self._max_concurrent} concurrent requests). "
                f"Try again later."
            )

        try:
            # Verify the server is ready for inference before forwarding.
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming(
                        "mlx_lm.server is reachable but model is still warming."
                    )
                raise RuntimeUnavailable(
                    f"mlx_lm.server is not ready: {health.detail}"
                )

            return await forward_non_streaming(
                self._server_url, request, timeout=300.0,
                model_override=self._config.model,
            )
        finally:
            self._concurrency_semaphore.release()

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        context: RequestExecutionContext | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Forward a streaming chat completion to mlx_lm.server.

        Acquires a concurrency slot; releases it when the stream
        completes, errors, or the client disconnects.
        """
        from whooshd.http_forwarding import (
            RuntimeOverloaded,
            RuntimeUnavailable,
            RuntimeWarming,
            forward_streaming,
        )
        from whooshd.config import get_runtime_acquire_timeout_seconds

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable(
                "MLX-LM Server runtime is not enabled or no model configured."
            )

        # Acquire concurrency slot.
        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(
                f"MLX-LM Server runtime at capacity ({self._max_concurrent} concurrent requests). "
                f"Try again later."
            )

        try:
            # Verify the server is ready for inference before forwarding.
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming(
                        "mlx_lm.server is reachable but model is still warming."
                    )
                raise RuntimeUnavailable(
                    f"mlx_lm.server is not ready: {health.detail}"
                )

            cancellation_token = context.cancellation_token if context else None
            async for chunk in forward_streaming(
                self._server_url, request, timeout=300.0,
                model_override=self._config.model,
                cancellation_token=cancellation_token,
            ):
                yield chunk
        finally:
            self._concurrency_semaphore.release()


# ── Internal helpers ────────────────────────────────────────────────────────


class _MlxLmServerHealthStatus:
    """Result of an mlx_lm.server health probe.

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
        models_data: dict | None = None,
    ) -> None:
        self.reachable = reachable
        self.runner_status = runner_status
        self.model_lifecycle = model_lifecycle
        self.detail = detail
        self.raw_status = raw_status
        self.models_data = models_data

    def __repr__(self) -> str:
        return (
            f"_MlxLmServerHealthStatus(reachable={self.reachable!r}, "
            f"runner_status={self.runner_status!r}, "
            f"model_lifecycle={self.model_lifecycle!r}, "
            f"detail={self.detail!r})"
        )


def _classify_health_exception(exc: Exception, timeout: float) -> _MlxLmServerHealthStatus:
    """Classify a health probe exception by type name and message.

    Uses string-based classification rather than typed except clauses
    so that tests can safely mock httpx without type errors.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    if "connecterror" in exc_name.lower() or "connection" in exc_msg:
        return _MlxLmServerHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="starting",
            detail="Connection refused — mlx_lm.server may be starting up.",
        )

    if "timeout" in exc_name.lower() or "timeout" in exc_msg:
        return _MlxLmServerHealthStatus(
            reachable=False,
            runner_status="ready",
            model_lifecycle="starting",
            detail=f"Health probe timed out after {timeout}s.",
        )

    return _MlxLmServerHealthStatus(
        reachable=False,
        runner_status="degraded",
        model_lifecycle="failed",
        detail=f"Unexpected health probe error: {exc}",
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


def _build_config_from_env() -> MlxLmServerConfig:
    """Build a MlxLmServerConfig from environment variables."""
    return MlxLmServerConfig(
        enabled=get_mlx_lm_server_enabled(),
        host=get_mlx_lm_server_host(),
        port=get_mlx_lm_server_port(),
        model=get_mlx_lm_server_model() or None,
        extra_args=get_mlx_lm_server_extra_args(),
        startup_timeout_seconds=get_mlx_lm_server_startup_timeout_seconds(),
        health_timeout_seconds=get_mlx_lm_server_health_timeout_seconds(),
    )

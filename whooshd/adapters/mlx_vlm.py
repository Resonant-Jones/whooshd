"""MLX-VLM adapter for vision-language models with supervised process lifecycle.

This adapter manages an ``mlx-vlm`` server as a subprocess backend,
parallel to how the MLX-LM Server adapter manages ``mlx_lm.server``.

Whoosh'd owns:
  * subprocess launch, readiness polling, and shutdown
  * health state classification
  * model discovery via proxy
  * image-containing chat completion forwarding
  * concurrency guardrails (default: 1 concurrent request)
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import AsyncIterator, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from whooshd.config import (
    get_mlx_vlm_enabled,
    get_mlx_vlm_extra_args,
    get_mlx_vlm_health_timeout_seconds,
    get_mlx_vlm_host,
    get_mlx_vlm_model,
    get_mlx_vlm_port,
    get_mlx_vlm_startup_timeout_seconds,
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
from whooshd.log_safety import exception_metadata

# Optional httpx import.
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


# ── Adapter configuration ────────────────────────────────────────────────────


class MlxVlmConfig(BaseModel):
    """Typed configuration for the MLX-VLM adapter."""

    enabled: bool = Field(False)
    host: str = Field("127.0.0.1")
    port: int = Field(8082, ge=1, le=65535)
    model: str | None = Field(None)
    extra_args: list[str] = Field(default_factory=list)
    startup_timeout_seconds: float = Field(60.0, ge=0.1)
    health_timeout_seconds: float = Field(2.0, ge=0.1)

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ── Error types ─────────────────────────────────────────────────────────────


class MlxVlmConfigError(Exception):
    """Raised when MLX-VLM configuration is invalid."""


class MlxVlmProcessError(Exception):
    """Raised when a supervised mlx-vlm process operation fails."""


# ── argv builder ────────────────────────────────────────────────────────────


def build_mlx_vlm_server_argv(config: MlxVlmConfig) -> list[str]:
    """Build a safe argv list for ``mlx-vlm`` server."""
    if not config.model:
        raise MlxVlmConfigError("A model path is required (set WHOOSHD_MLX_VLM_MODEL)")

    argv: list[str] = [
        "python", "-m", "mlx_vlm", "server",
        "--model", config.model,
        "--host", config.host,
        "--port", str(config.port),
    ]
    if config.extra_args:
        argv.extend(config.extra_args)

    logger.info(
        "mlx_vlm.process.argv_built model_path_present=%s host=%s port=%s",
        bool(config.model), config.host, config.port,
    )
    return argv


# ── Managed process wrapper ─────────────────────────────────────────────────


class ManagedMlxVlmServer:
    """Lightweight subprocess wrapper for a locally-managed mlx-vlm server."""

    def __init__(self, config: MlxVlmConfig) -> None:
        self._config = config
        self._process: subprocess.Popen[str] | None = None
        self._started_at: float | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> int | None:
        return self._process.poll() if self._process else None

    def start(self) -> subprocess.Popen[str]:
        if self.is_running:
            raise MlxVlmProcessError(f"mlx-vlm already running (pid={self.pid})")
        argv = build_mlx_vlm_server_argv(self._config)
        logger.info(
            "mlx_vlm.process.starting model_path_present=%s port=%s",
            bool(self._config.model), self._config.port,
        )
        try:
            self._process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, shell=False,
            )
        except OSError as exc:
            raise MlxVlmProcessError("Failed to launch mlx-vlm") from exc
        self._started_at = time.monotonic()
        logger.info("mlx_vlm.process.started pid=%s", getattr(self._process, "pid", None))
        return self._process

    def stop(self, timeout: float = 15.0) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            self._process = None; self._started_at = None; return
        _pid = getattr(self._process, "pid", None)
        logger.info("mlx_vlm.process.stopping pid=%s", _pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("mlx_vlm.process.force_kill pid=%s", _pid)
            self._process.kill()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.error("mlx_vlm.process.kill_failed pid=%s", _pid)
        self._process = None; self._started_at = None

    def restart(self) -> subprocess.Popen[str]:
        self.stop(); return self.start()

    async def wait_until_ready(self, health_fn, startup_timeout: float,
                                probe_interval: float = 1.0) -> None:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                raise MlxVlmProcessError(
                    f"mlx-vlm exited unexpectedly with code {self.returncode}")
            status = await health_fn()
            if status.reachable:
                return
            await asyncio.sleep(probe_interval)
        logger.error("mlx_vlm.process.startup_timeout pid=%s", self.pid)
        self.stop()
        raise MlxVlmProcessError(f"mlx-vlm did not become ready within {startup_timeout}s")

    def check_exited(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is not None:
            logger.warning("mlx_vlm.process.exited pid=%s returncode=%s",
                           self._process.pid, self._process.returncode)
            self._process = None; self._started_at = None
            return True
        return False


# ── Health status record ────────────────────────────────────────────────────


class _MlxVlmHealthStatus:
    def __init__(self, *, reachable: bool, runner_status: str,
                 model_lifecycle: str, detail: str,
                 raw_status: int | None = None) -> None:
        self.reachable = reachable
        self.runner_status = runner_status
        self.model_lifecycle = model_lifecycle
        self.detail = detail
        self.raw_status = raw_status


def _classify_health_exception(exc: Exception, timeout: float) -> _MlxVlmHealthStatus:
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()
    if "connect" in exc_name.lower() or "connection" in exc_msg:
        return _MlxVlmHealthStatus(reachable=False, runner_status="ready",
                                    model_lifecycle="starting",
                                    detail="Connection refused — mlx-vlm may be starting up.")
    if "timeout" in exc_name.lower() or "timeout" in exc_msg:
        return _MlxVlmHealthStatus(reachable=False, runner_status="ready",
                                    model_lifecycle="starting",
                                    detail=f"Health probe timed out after {timeout}s.")
    return _MlxVlmHealthStatus(reachable=False, runner_status="degraded",
                                model_lifecycle="failed",
                                detail=f"Unexpected health probe failure ({exception_metadata(exc)})")


# ── Adapter class ───────────────────────────────────────────────────────────


class MlxVlmAdapter:
    """MLX-VLM inference adapter for vision-language models.

    Manages an mlx-vlm server subprocess and forwards chat completion
    requests including image content.
    """

    def __init__(self, config: MlxVlmConfig | None = None) -> None:
        self._config = config or _build_config_from_env()
        self._managed_process: ManagedMlxVlmServer | None = None

        from whooshd.config import get_mlx_vlm_max_concurrent_requests
        self._max_concurrent = get_mlx_vlm_max_concurrent_requests()
        self._concurrency_semaphore = asyncio.Semaphore(self._max_concurrent)

    # ── Protocol properties ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "mlx-vlm"

    @property
    def kind(self) -> str:
        return RuntimeKind.MLX_VLM.value

    @property
    def supports_streaming(self) -> bool:
        return True

    # ── Configuration ──────────────────────────────────────────────────

    @property
    def config(self) -> MlxVlmConfig:
        return self._config

    @property
    def _server_url(self) -> str:
        return self._config.server_url

    # ── Health probing ─────────────────────────────────────────────────

    async def check_health(self) -> _MlxVlmHealthStatus:
        if not self._config.enabled:
            return _MlxVlmHealthStatus(reachable=False, runner_status="ready",
                                        model_lifecycle="unloaded",
                                        detail="MLX-VLM runtime is not enabled.")
        if not self._config.model:
            return _MlxVlmHealthStatus(reachable=False, runner_status="ready",
                                        model_lifecycle="unloaded",
                                        detail="No model configured for MLX-VLM.")
        proc = self._managed_process
        if proc is not None:
            proc.check_exited()
            if not proc.is_running:
                return _MlxVlmHealthStatus(reachable=False, runner_status="degraded",
                                            model_lifecycle="failed",
                                            detail="mlx-vlm process exited unexpectedly.")
            return await self._probe_server(self._server_url)
        return await self._probe_server(self._server_url)

    async def _probe_server(self, url: str) -> _MlxVlmHealthStatus:
        if httpx is None:
            return _MlxVlmHealthStatus(reachable=False, runner_status="ready",
                                        model_lifecycle="unloaded",
                                        detail="httpx not installed.")
        timeout = self._config.health_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}/v1/models")
                if resp.status_code == 200:
                    return _MlxVlmHealthStatus(reachable=True, runner_status="ready",
                                                model_lifecycle="ready",
                                                detail="mlx-vlm /v1/models returned 200.",
                                                raw_status=200)
                return _MlxVlmHealthStatus(reachable=True, runner_status="ready",
                                            model_lifecycle="warming",
                                            detail=f"mlx-vlm reachable but /v1/models returned {resp.status_code}.",
                                            raw_status=resp.status_code)
        except Exception as exc:
            return _classify_health_exception(exc, timeout)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def is_loaded(self) -> bool:
        if self._managed_process is not None:
            self._managed_process.check_exited()
            return self._managed_process.is_running
        return False

    def model_id(self) -> Optional[str]:
        return self._config.model

    async def warmup(self) -> None:
        if not self._config.enabled:
            raise MlxVlmConfigError("MLX-VLM is not enabled.")
        if not self._config.model:
            raise MlxVlmConfigError("No model configured for MLX-VLM.")
        if self._managed_process is None:
            status = await self.check_health()
            if not status.reachable:
                raise MlxVlmProcessError("mlx-vlm is not reachable")
            return
        proc = self._managed_process
        proc.check_exited()
        if not proc.is_running:
            proc.start()
            await proc.wait_until_ready(health_fn=self.check_health,
                                         startup_timeout=self._config.startup_timeout_seconds)
        else:
            await self.check_health()

    async def unload(self) -> None:
        if self._managed_process is not None:
            self._managed_process.stop()
            self._managed_process = None

    # ── Multi-runtime introspection ────────────────────────────────────

    async def health(self) -> RuntimeHealth:
        if not self._config.enabled:
            return RuntimeHealth(kind=self.kind, enabled=False,
                                 state=RuntimeHealthState.OFFLINE,
                                 active_model=None, detail="MLX-VLM runtime is disabled.")
        status = await self.check_health()
        state_map = {"ready": RuntimeHealthState.READY, "warming": RuntimeHealthState.MODEL_WARMING,
                     "unloaded": RuntimeHealthState.OFFLINE, "failed": RuntimeHealthState.ERROR,
                     "degraded": RuntimeHealthState.DEGRADED, "starting": RuntimeHealthState.STARTING}
        state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE)
        return RuntimeHealth(kind=self.kind, enabled=True, state=state,
                             active_model=self._config.model if self.is_loaded() else None,
                             detail=status.detail)

    async def list_models(self) -> list[RuntimeModel]:
        if not self._config.model:
            return []
        loaded = self.is_loaded()
        status = await self.check_health()
        state_map = {"ready": RuntimeHealthState.READY.value, "warming": RuntimeHealthState.MODEL_WARMING.value,
                     "unloaded": RuntimeHealthState.OFFLINE.value, "failed": RuntimeHealthState.ERROR.value,
                     "degraded": RuntimeHealthState.DEGRADED.value}
        model_state = state_map.get(status.model_lifecycle, RuntimeHealthState.OFFLINE.value)
        model_id = self._config.model
        display_name = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id
        return [RuntimeModel(id=model_id, display_name=display_name, runtime=self.kind,
                             format="mlx", path=model_id, context_window=None,
                             supports_tools=False, supports_vision=True, supports_reasoning=False,
                             loaded=loaded, state=model_state)]

    # ── Inference ──────────────────────────────────────────────────────

    async def chat_completion(self, request: ChatCompletionRequest,
                              context: RequestExecutionContext | None = None) -> ChatCompletionResponse:
        from whooshd.http_forwarding import (RuntimeOverloaded, RuntimeUnavailable,
                                              RuntimeWarming, forward_non_streaming)
        from whooshd.config import get_runtime_acquire_timeout_seconds

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable("MLX-VLM runtime is not enabled or no model configured.")

        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(f"MLX-VLM runtime at capacity ({self._max_concurrent} concurrent).")
        try:
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming("mlx-vlm is warming.")
                raise RuntimeUnavailable("mlx-vlm not ready")
            return await forward_non_streaming(self._server_url, request, timeout=300.0,
                                                model_override=self._config.model)
        finally:
            self._concurrency_semaphore.release()

    async def chat_completion_stream(self, request: ChatCompletionRequest,
                                      context: RequestExecutionContext | None = None) -> AsyncIterator[ChatCompletionChunk]:
        from whooshd.http_forwarding import (RuntimeOverloaded, RuntimeUnavailable,
                                              RuntimeWarming, forward_streaming)
        from whooshd.config import get_runtime_acquire_timeout_seconds

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable("MLX-VLM runtime is not enabled or no model configured.")

        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(f"MLX-VLM runtime at capacity ({self._max_concurrent} concurrent).")
        try:
            health = await self.check_health()
            if not health.reachable:
                if health.model_lifecycle == "warming":
                    raise RuntimeWarming("mlx-vlm is warming.")
                raise RuntimeUnavailable("mlx-vlm not ready")
            cancellation_token = context.cancellation_token if context else None
            async for chunk in forward_streaming(self._server_url, request, timeout=300.0,
                                                  model_override=self._config.model,
                                                  cancellation_token=cancellation_token):
                yield chunk
        finally:
            self._concurrency_semaphore.release()

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        from whooshd.http_forwarding import (RuntimeOverloaded, RuntimeUnavailable,
                                              RuntimeWarming, forward_non_streaming)
        from whooshd.config import get_runtime_acquire_timeout_seconds
        import uuid as _uuid

        if not self._config.enabled or not self._config.model:
            raise RuntimeUnavailable("MLX-VLM runtime is not enabled or no model configured.")

        timeout = get_runtime_acquire_timeout_seconds()
        acquired = await _acquire_slot(self._concurrency_semaphore, timeout)
        if not acquired:
            raise RuntimeOverloaded(f"MLX-VLM runtime at capacity ({self._max_concurrent} concurrent).")
        try:
            health = await self.check_health()
            if not health.reachable:
                raise RuntimeUnavailable("mlx-vlm not ready")
            chat_req = ChatCompletionRequest(
                model=self._config.model,
                messages=[ChatMessage(role="user", content=request.prompt)],
                temperature=request.temperature, max_tokens=request.max_tokens, stream=False)
            chat_resp = await forward_non_streaming(self._server_url, chat_req, timeout=300.0,
                                                     model_override=self._config.model)
            content = chat_resp.choices[0].message.content if chat_resp.choices else ""
            return GenerateResponse(ok=True, request_id=request.request_id or str(_uuid.uuid4()),
                                    model_id=chat_resp.model, text=content,
                                    finish_reason=chat_resp.choices[0].finish_reason if chat_resp.choices else "stop",
                                    usage=TokenUsage(prompt_tokens=chat_resp.usage.prompt_tokens if chat_resp.usage else None,
                                                     completion_tokens=chat_resp.usage.completion_tokens if chat_resp.usage else None,
                                                     total_tokens=chat_resp.usage.total_tokens if chat_resp.usage else None),
                                    runtime=ResponseRuntimeInfo(adapter=self.name, queued=False, elapsed_ms=0.0))
        finally:
            self._concurrency_semaphore.release()


# ── Concurrency guard helper ──────────────────────────────────────────────


async def _acquire_slot(semaphore, timeout: float) -> bool:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _build_config_from_env() -> MlxVlmConfig:
    return MlxVlmConfig(
        enabled=get_mlx_vlm_enabled(),
        host=get_mlx_vlm_host(),
        port=get_mlx_vlm_port(),
        model=get_mlx_vlm_model() or None,
        extra_args=get_mlx_vlm_extra_args(),
        startup_timeout_seconds=get_mlx_vlm_startup_timeout_seconds(),
        health_timeout_seconds=get_mlx_vlm_health_timeout_seconds(),
    )

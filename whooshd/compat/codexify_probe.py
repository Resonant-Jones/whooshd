"""Codexify provider-compatibility probe.

Mimics the behaviour of Codexify's MLXRunnerClient so the contract
can be validated before real inference is wired in.

The probe is test-facing for now.  Later it can grow a thin CLI wrapper
when Codexify desktop needs a local smoke-test tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import httpx

# ── Streaming parser ────────────────────────────────────────────────────────


def reconstruct_assistant_text(sse_body: str) -> str:
    """Parse an OpenAI-compatible SSE stream body into visible assistant text.

    Rules (matching Codexify's MLXRunnerClient expectations):
      * Only process lines beginning with ``data: ``.
      * Stop immediately on ``data: [DONE]``.
      * Parse each data payload as JSON.
      * Extract ``choices[0].delta.content`` only.
      * Skip chunks where content is null/absent (role marker, finish marker).
      * Never expose reasoning, metadata, or internal fields as assistant text.
    """
    tokens: list[str] = []

    for line in sse_body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]  # strip "data: " prefix

        if payload == "[DONE]":
            break

        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        choices = chunk.get("choices", [])
        if not choices:
            continue

        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            tokens.append(content)

    return "".join(tokens)


# ── Probe result types ──────────────────────────────────────────────────────


@dataclass
class HealthProbe:
    """Result of a /health probe."""

    ok: bool = False
    status: str = ""
    active_model: Optional[str] = None
    queue_depth: int = 0
    active_jobs: int = 0
    memory_pressure: str = "unknown"


@dataclass
class RuntimeProbe:
    """Result of a /runtime probe."""

    memory_pressure: str = "unknown"
    total_gb: float = 0.0
    available_gb: float = 0.0
    loaded_model_count: int = 0
    max_active_jobs: int = 0
    safe_concurrency: int = 0
    uptime_seconds: float = 0.0


@dataclass
class ModelInventoryProbe:
    """Result of a model-inventory probe (OpenAI or Ollama format)."""

    model_ids: list[str] = field(default_factory=list)
    format: str = "openai"  # "openai" or "ollama"


@dataclass
class CompletionProbe:
    """Result of a non-streaming chat completion probe."""

    ok: bool = False
    model: str = ""
    content: str = ""
    finish_reason: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


@dataclass
class StreamProbe:
    """Result of a streaming chat completion probe."""

    ok: bool = False
    model: str = ""
    visible_text: str = ""
    chunk_count: int = 0
    content_type: str = ""


# ── Probe client ────────────────────────────────────────────────────────────


class CodexifyProbe:
    """Lightweight client that mimics Codexify's local-provider expectations.

    All methods accept an ``httpx.AsyncClient`` so tests can inject an
    ASGI transport and future CLI usage can inject a real HTTP transport.
    """

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    # ── Health ──────────────────────────────────────────────────────────

    async def probe_health(self) -> HealthProbe:
        """Call GET /health and return a typed probe result."""
        resp = await self._client.get("/health")
        body = resp.json()
        mem = body.get("memory", {})
        return HealthProbe(
            ok=body.get("ok", False),
            status=body.get("status", ""),
            active_model=body.get("active_model"),
            queue_depth=body.get("queue_depth", 0),
            active_jobs=body.get("active_jobs", 0),
            memory_pressure=mem.get("pressure", "unknown"),
        )

    # ── Runtime ─────────────────────────────────────────────────────────

    async def probe_runtime(self) -> RuntimeProbe:
        """Call GET /runtime and return a typed probe result."""
        resp = await self._client.get("/runtime")
        body = resp.json()
        mem = body.get("memory", {})
        cc = body.get("concurrency", {})
        return RuntimeProbe(
            memory_pressure=mem.get("pressure", "unknown"),
            total_gb=float(mem.get("total_gb", 0)),
            available_gb=float(mem.get("available_gb", 0)),
            loaded_model_count=len(body.get("loaded_models", [])),
            max_active_jobs=int(cc.get("max_active_jobs", 0)),
            safe_concurrency=int(cc.get("estimated_safe_concurrency", 0)),
            uptime_seconds=float(body.get("uptime_seconds", 0)),
        )

    # ── Model inventory ─────────────────────────────────────────────────

    async def probe_models_openai(self) -> ModelInventoryProbe:
        """Call GET /v1/models and extract usable model IDs."""
        resp = await self._client.get("/v1/models")
        body = resp.json()
        ids = [m["id"] for m in body.get("data", [])]
        return ModelInventoryProbe(model_ids=ids, format="openai")

    async def probe_models_ollama(self) -> ModelInventoryProbe:
        """Call GET /api/tags and extract usable model names."""
        resp = await self._client.get("/api/tags")
        body = resp.json()
        ids = [m["name"] for m in body.get("models", [])]
        return ModelInventoryProbe(model_ids=ids, format="ollama")

    # ── Non-streaming chat ──────────────────────────────────────────────

    async def probe_chat_completion(
        self,
        model: str,
        messages: list[dict],
    ) -> CompletionProbe:
        """Call POST /v1/chat/completions (non-streaming)."""
        resp = await self._client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
        )
        body = resp.json()
        choices = body.get("choices", [])
        usage = body.get("usage", {})
        content = ""
        finish = ""
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            finish = choices[0].get("finish_reason", "")
        return CompletionProbe(
            ok=resp.status_code == 200,
            model=body.get("model", ""),
            content=content,
            finish_reason=finish,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    # ── Streaming chat ──────────────────────────────────────────────────

    async def probe_chat_stream(
        self,
        model: str,
        messages: list[dict],
    ) -> StreamProbe:
        """Call POST /v1/chat/completions (streaming) and reconstruct text."""
        resp = await self._client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
        )
        content_type = resp.headers.get("content-type", "")
        visible_text = reconstruct_assistant_text(resp.text)

        # Count chunks: every data: line that isn't [DONE].
        chunk_count = 0
        for line in resp.text.splitlines():
            if line.startswith("data: ") and line[6:] != "[DONE]":
                chunk_count += 1

        return StreamProbe(
            ok=resp.status_code == 200,
            model=model,
            visible_text=visible_text,
            chunk_count=chunk_count,
            content_type=content_type,
        )

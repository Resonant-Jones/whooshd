#!/usr/bin/env python3
"""Codexify Provider Smoke Script for Whoosh'd.

Simulates how Codexify should validate and use Whoosh'd as a local
provider.  Validates health, runtime identity, model discovery,
streaming chat, and optional vision smoke — all over HTTP.

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
  2 = could not connect or blocked

Usage:
  python scripts/codexify_provider_smoke.py --base-url http://127.0.0.1:8000 \\
      --model llama-3.2-3b-mlx --expect-runtime mlx_lm_server

  python scripts/codexify_provider_smoke.py --base-url http://127.0.0.1:8000 \\
      --model qwen2.5-0.5b-gguf --expect-runtime llama_cpp

  python scripts/codexify_provider_smoke.py --base-url http://127.0.0.1:8000 \\
      --model qwen2-vl-2b-mlx --expect-runtime mlx_vlm \\
      --image tests/fixtures/vision/color_shapes.png \\
      --vision-prompt "What shapes and colors are visible?"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    import httpx
except ImportError:
    print("httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(2)


# ── Exit codes ──────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2


# ── Check result ────────────────────────────────────────────────────────────


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass
class Check:
    name: str
    status: Status = Status.PASS
    detail: str = ""
    duration_ms: float = 0.0


# ── Codexify-compatible SSE parser ──────────────────────────────────────────


def parse_codexify_sse(body: str) -> tuple[str, int, bool]:
    """Parse an OpenAI-compatible SSE stream.

    Returns (visible_text, chunk_count, has_done).
    """
    tokens: list[str] = []
    chunk_count = 0
    has_done = False

    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            has_done = True
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        chunk_count += 1
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            tokens.append(content)

    return "".join(tokens), chunk_count, has_done


# ── Health / runtime checks ────────────────────────────────────────────────


async def check_health(client: httpx.AsyncClient) -> Check:
    t0 = time.monotonic()
    try:
        resp = await client.get("/health")
        body = resp.json()
        ok = resp.status_code == 200 and body.get("ok") is True
        return Check("/health", Status.PASS if ok else Status.FAIL,
                     f"status={resp.status_code} lifecycle={body.get('model_lifecycle','?')}",
                     (time.monotonic() - t0) * 1000)
    except Exception as exc:
        return Check("/health", Status.FAIL, str(exc))


async def check_health_runtime_stable(client: httpx.AsyncClient,
                                       expect_runtime: str | None = None) -> tuple[Check, str, str]:
    """Check /health/runtime and verify session stability.

    Returns (check, session_id, runtime_kind).
    """
    t0 = time.monotonic()

    async def _get():
        resp = await client.get("/health/runtime")
        return resp.json() if resp.status_code == 200 else None

    body1 = await _get()
    if body1 is None:
        return Check("/health/runtime", Status.FAIL, "unreachable"), "", ""

    body2 = await _get()
    if body2 is None:
        return Check("/health/runtime", Status.FAIL, "unreachable on second call"), "", ""

    session1 = body1.get("session", {})
    session2 = body2.get("session", {})

    sid1 = session1.get("session_id", "")
    sid2 = session2.get("session_id", "")

    if sid1 != sid2:
        return Check("/health/runtime session", Status.FAIL,
                     f"unstable: {sid1} -> {sid2}"), sid1, ""

    runtimes = body1.get("runtimes", {})
    reported_kinds = [k for k in runtimes if k != "stub"]
    runtime_kind = reported_kinds[0] if reported_kinds else ""

    # Check expected runtime.
    if expect_runtime:
        if expect_runtime not in runtimes:
            kinds = list(runtimes.keys())
            return Check("/health/runtime", Status.FAIL,
                         f"expected runtime '{expect_runtime}' not found. Server has: {kinds}"), sid1, runtime_kind

    detail = f"session_id={sid1} pid={session1.get('pid','?')} runtimes={list(runtimes.keys())}"
    return Check("/health/runtime", Status.PASS, detail, (time.monotonic() - t0) * 1000), sid1, runtime_kind


async def check_ready(client: httpx.AsyncClient) -> Check:
    t0 = time.monotonic()
    try:
        resp = await client.get("/ready")
        body = resp.json()
        ready = body.get("ready", False)
        return Check("/ready", Status.PASS if ready else Status.FAIL,
                     f"ready={ready} reason={body.get('reason','')}",
                     (time.monotonic() - t0) * 1000)
    except Exception as exc:
        return Check("/ready", Status.FAIL, str(exc))


# ── Model discovery ────────────────────────────────────────────────────────


async def check_models(client: httpx.AsyncClient, expected_model: str) -> Check:
    t0 = time.monotonic()
    try:
        resp = await client.get("/v1/models")
        data = resp.json().get("data", [])
        ids = [m["id"] for m in data]
        if expected_model in ids:
            return Check("/v1/models", Status.PASS,
                         f"found '{expected_model}' among {len(ids)} models")
        return Check("/v1/models", Status.FAIL,
                     f"'{expected_model}' not found. Models: {ids}")
    except Exception as exc:
        return Check("/v1/models", Status.FAIL, str(exc))


async def check_tags(client: httpx.AsyncClient, expected_model: str) -> Check:
    t0 = time.monotonic()
    try:
        resp = await client.get("/api/tags")
        models = resp.json().get("models", [])
        names = [m["name"] for m in models]
        if expected_model in names:
            return Check("/api/tags", Status.PASS,
                         f"found '{expected_model}' among {len(names)} tags")
        return Check("/api/tags", Status.FAIL,
                     f"'{expected_model}' not found. Tags: {names}")
    except Exception as exc:
        return Check("/api/tags", Status.FAIL, str(exc))


# ── Streaming chat ──────────────────────────────────────────────────────────


async def check_streaming_chat(client: httpx.AsyncClient, model: str,
                                messages: list[dict]) -> Check:
    t0 = time.monotonic()
    try:
        resp = await client.post("/v1/chat/completions", json={
            "model": model, "messages": messages, "stream": True,
        }, timeout=300.0)
        if resp.status_code != 200:
            return Check("POST /v1/chat/completions (streaming)", Status.FAIL,
                         f"HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            return Check("POST /v1/chat/completions (streaming)", Status.FAIL,
                         f"wrong content-type: {content_type}")
        text, chunks, has_done = parse_codexify_sse(resp.text)
        issues = []
        if not has_done:
            issues.append("missing [DONE]")
        if chunks == 0:
            issues.append("no chunks")
        if not text.strip():
            issues.append("no visible text")
        status = Status.PASS if not issues else Status.FAIL
        return Check("POST /v1/chat/completions (streaming)", status,
                     f"chunks={chunks} done={has_done} text_len={len(text)}"
                     + (f" issues={issues}" if issues else ""),
                     (time.monotonic() - t0) * 1000)
    except Exception as exc:
        return Check("POST /v1/chat/completions (streaming)", Status.FAIL, str(exc))


# ── Main runner ─────────────────────────────────────────────────────────────


def _build_image_messages(image_path: str, prompt: str) -> list[dict]:
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
    ]}]


async def run_smoke(base_url: str, model: str, expect_runtime: str | None = None,
                    image_path: str | None = None, vision_prompt: str = "",
                    _client: httpx.AsyncClient | None = None) -> tuple[list[Check], int]:
    """Run the full Codexify provider smoke suite.

    _client is for testing only — inject a mock client.
    """
    checks: list[Check] = []
    url = base_url.rstrip("/")

    async def _with_client(client):
        # Health.
        checks.append(await check_health(client))
        chk, sid, runtime_kind = await check_health_runtime_stable(client, expect_runtime)
        checks.append(chk)
        checks.append(await check_ready(client))

        # Model discovery.
        checks.append(await check_models(client, model))
        checks.append(await check_tags(client, model))

        # Streaming chat.
        if image_path:
            msgs = _build_image_messages(image_path, vision_prompt or "Describe this image.")
        else:
            msgs = [{"role": "user", "content": "Say hello in one short sentence."}]
        checks.append(await check_streaming_chat(client, model, msgs))

    if _client is not None:
        await _with_client(_client)
    else:
        async with httpx.AsyncClient(base_url=url, timeout=30.0) as client:
            await _with_client(client)

    failed = any(c.status == Status.FAIL for c in checks)
    exit_code = EXIT_FAIL if failed else EXIT_OK
    return checks, exit_code


def print_table(checks: list[Check]) -> None:
    nw = max(len(c.name) for c in checks) + 2
    print(f"\n{'Check':<{nw}} {'Status':<10} Detail")
    print("-" * (nw + 50))
    for c in checks:
        print(f"{c.name:<{nw}} {c.status.value.upper():<10} {c.detail}")
    passed = sum(1 for c in checks if c.status == Status.PASS)
    failed = sum(1 for c in checks if c.status == Status.FAIL)
    print(f"\n{passed} passed, {failed} failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Codexify Provider Smoke Script for Whoosh'd")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True, help="Expected model alias")
    parser.add_argument("--expect-runtime", default=None,
                        choices=["mlx_lm_server", "mlx_vlm", "llama_cpp"],
                        help="Expected runtime kind")
    parser.add_argument("--image", default=None, help="Image for vision smoke")
    parser.add_argument("--vision-prompt", default="Describe this image in one short sentence.")
    args = parser.parse_args()

    checks, exit_code = asyncio.run(run_smoke(
        args.base_url, args.model, args.expect_runtime,
        args.image, args.vision_prompt,
    ))
    print_table(checks)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

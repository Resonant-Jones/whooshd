#!/usr/bin/env python3
"""Whoosh'd Local Runtime Validation Harness.

Validates Whoosh'd against configured local runtimes (llama.cpp, MLX-LM Server)
with Codexify-compatible streaming verification.

Usage::

    # Validate llama.cpp runtime
    python scripts/validate_local_runtimes.py --runtime llama-cpp

    # Validate MLX-LM Server runtime
    python scripts/validate_local_runtimes.py --runtime mlx-lm-server

    # Validate both
    python scripts/validate_local_runtimes.py --runtime both

    # With custom Whoosh'd URL and model
    python scripts/validate_local_runtimes.py --runtime both \\
        --whooshd-url http://127.0.0.1:8000 \\
        --model my-model

    # Concurrent streaming smoke test
    python scripts/validate_local_runtimes.py --runtime both --concurrency 2

    # JSON output
    python scripts/validate_local_runtimes.py --runtime both --json

Exit codes::

    0 = all required checks passed
    1 = required checks failed
    2 = validation blocked (missing dependency, unavailable runtime)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ── Try importing httpx ─────────────────────────────────────────────────────

try:
    import httpx
except ImportError:
    print("httpx is required. Install it with: pip install httpx", file=sys.stderr)
    sys.exit(2)

# ── Exit codes ──────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2


# ── Check status enum ───────────────────────────────────────────────────────


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    SKIP = "skip"


@dataclass
class CheckResult:
    """A single validation check result."""

    name: str
    status: CheckStatus
    detail: str = ""
    duration_ms: float = 0.0


# ── Concurrent streaming result ─────────────────────────────────────────────


@dataclass
class ConcurrentStreamResult:
    """Result of a single concurrent streaming request."""

    request_id: int
    accepted_at: float = 0.0
    first_token_at: float = 0.0
    completed_at: float = 0.0
    ttft_ms: float = 0.0
    total_ms: float = 0.0
    status: str = "unknown"
    visible_text: str = ""
    chunk_count: int = 0


# ── Codexify-compatible SSE parser ──────────────────────────────────────────


def parse_codexify_sse(body: str) -> tuple[str, int, bool]:
    """Parse an OpenAI-compatible SSE stream body the way Codexify does.

    Returns:
        (visible_text, chunk_count, has_done_sentinel)

    Rules (matching Codexify's MLXRunnerClient expectations):
      * Only process lines beginning with ``data: ``.
      * Stop immediately on ``data: [DONE]``.
      * Parse each data payload as JSON.
      * Extract ``choices[0].delta.content`` only.
      * Skip chunks where content is null/absent (role marker, finish marker).
      * Never expose reasoning, metadata, or internal fields as assistant text.
    """
    tokens: list[str] = []
    chunk_count = 0
    has_done = False

    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]  # strip "data: " prefix

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


# ── Dependency detection ────────────────────────────────────────────────────


def _detect_dependency(runtime: str) -> CheckResult:
    """Detect whether the required runtime dependencies are available.

    Returns a CheckResult — BLOCKED if missing, PASS if found.
    """
    if runtime == "llama-cpp":
        # Check for llama-server binary.
        binary = os.environ.get("WHOOSHD_LLAMA_CPP_BINARY_PATH", "llama-server")
        try:
            result = subprocess.run(
                ["which", binary], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                return CheckResult(
                    name="llama-server binary",
                    status=CheckStatus.PASS,
                    detail=f"Found at {path}",
                )
            else:
                return CheckResult(
                    name="llama-server binary",
                    status=CheckStatus.BLOCKED,
                    detail=f"llama-server not found (searched for '{binary}')",
                )
        except Exception as exc:
            return CheckResult(
                name="llama-server binary",
                status=CheckStatus.BLOCKED,
                detail=f"Error checking for llama-server: {exc}",
            )

    elif runtime == "mlx-lm-server":
        # Check for mlx-lm Python package.
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import mlx_lm; print(mlx_lm.__version__)"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return CheckResult(
                    name="mlx-lm package",
                    status=CheckStatus.PASS,
                    detail=f"mlx-lm {version} installed",
                )
            else:
                return CheckResult(
                    name="mlx-lm package",
                    status=CheckStatus.BLOCKED,
                    detail="mlx-lm is not installed. Install with: pip install mlx-lm",
                )
        except Exception as exc:
            return CheckResult(
                name="mlx-lm package",
                status=CheckStatus.BLOCKED,
                detail=f"Error checking mlx-lm: {exc}",
            )

    elif runtime == "mlx-vlm":
        # Check for mlx-vlm Python package.
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import mlx_vlm; print('ok')"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return CheckResult(
                    name="mlx-vlm package",
                    status=CheckStatus.PASS,
                    detail="mlx-vlm installed",
                )
            else:
                return CheckResult(
                    name="mlx-vlm package",
                    status=CheckStatus.BLOCKED,
                    detail="mlx-vlm is not installed. Install with: pip install mlx-vlm",
                )
        except Exception as exc:
            return CheckResult(
                name="mlx-vlm package",
                status=CheckStatus.BLOCKED,
                detail=f"Error checking mlx-vlm: {exc}",
            )

    return CheckResult(
        name="unknown runtime",
        status=CheckStatus.BLOCKED,
        detail=f"Unknown runtime: {runtime}",
    )


def detect_dependencies(runtimes: list[str]) -> list[CheckResult]:
    """Detect dependencies for all requested runtimes."""
    results: list[CheckResult] = []
    seen: set[str] = set()
    for rt in runtimes:
        if rt == "both":
            for sub in ["llama-cpp", "mlx-lm-server"]:
                if sub not in seen:
                    results.append(_detect_dependency(sub))
                    seen.add(sub)
        else:
            if rt not in seen:
                results.append(_detect_dependency(rt))
                seen.add(rt)
    return results


# ── HTTP validation helpers ─────────────────────────────────────────────────


async def _check_health(client: httpx.AsyncClient) -> CheckResult:
    """Check GET /health."""
    t0 = time.monotonic()
    try:
        resp = await client.get("/health")
        body = resp.json()
        ok = resp.status_code == 200 and body.get("ok") is True
        return CheckResult(
            name="/health",
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            detail=f"status={resp.status_code} runner={body.get('runner','?')} lifecycle={body.get('model_lifecycle','?')}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="/health",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_health_runtime(client: httpx.AsyncClient) -> CheckResult:
    """Check GET /health/runtime."""
    t0 = time.monotonic()
    try:
        resp = await client.get("/health/runtime")
        body = resp.json()
        runtimes = body.get("runtimes", {})
        non_stub = {k: v for k, v in runtimes.items() if k != "stub"}
        if not non_stub:
            return CheckResult(
                name="/health/runtime",
                status=CheckStatus.FAIL,
                detail="No non-stub runtimes registered.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        details = ", ".join(
            f"{k}={v.get('state','?')}" for k, v in sorted(non_stub.items())
        )
        aggregate = body.get("status", "?")
        return CheckResult(
            name="/health/runtime",
            status=CheckStatus.PASS if aggregate in ("ok", "degraded") else CheckStatus.FAIL,
            detail=f"status={aggregate} runtimes=({details})",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="/health/runtime",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_ready(client: httpx.AsyncClient) -> CheckResult:
    """Check GET /ready."""
    t0 = time.monotonic()
    try:
        resp = await client.get("/ready")
        body = resp.json()
        ready = body.get("ready", False)
        reason = body.get("reason", "")
        return CheckResult(
            name="/ready",
            status=CheckStatus.PASS if ready else CheckStatus.FAIL,
            detail=f"ready={ready}" + (f" reason={reason}" if reason else ""),
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="/ready",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_models(client: httpx.AsyncClient) -> CheckResult:
    """Check GET /v1/models."""
    t0 = time.monotonic()
    try:
        resp = await client.get("/v1/models")
        body = resp.json()
        data = body.get("data", [])
        model_ids = [m["id"] for m in data]
        return CheckResult(
            name="/v1/models",
            status=CheckStatus.PASS if len(model_ids) >= 1 else CheckStatus.FAIL,
            detail=f"{len(model_ids)} models: {', '.join(model_ids[:5])}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="/v1/models",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_ollama_tags(client: httpx.AsyncClient) -> CheckResult:
    """Check GET /api/tags."""
    t0 = time.monotonic()
    try:
        resp = await client.get("/api/tags")
        body = resp.json()
        models = body.get("models", [])
        names = [m["name"] for m in models]
        return CheckResult(
            name="/api/tags",
            status=CheckStatus.PASS if len(names) >= 1 else CheckStatus.FAIL,
            detail=f"{len(names)} tags: {', '.join(names[:5])}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="/api/tags",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_non_streaming_chat(
    client: httpx.AsyncClient, model: str, messages: list[dict]
) -> CheckResult:
    """Check POST /v1/chat/completions (non-streaming)."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
            timeout=300.0,
        )
        body = resp.json()
        if resp.status_code == 200:
            choices = body.get("choices", [])
            content = ""
            finish = ""
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                finish = choices[0].get("finish_reason", "")
            has_content = len(content.strip()) > 0
            return CheckResult(
                name="POST /v1/chat/completions (non-streaming)",
                status=CheckStatus.PASS if has_content else CheckStatus.FAIL,
                detail=f"status={resp.status_code} finish={finish} content_len={len(content)}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        else:
            return CheckResult(
                name="POST /v1/chat/completions (non-streaming)",
                status=CheckStatus.FAIL,
                detail=f"status={resp.status_code} body={json.dumps(body)[:120]}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
    except Exception as exc:
        return CheckResult(
            name="POST /v1/chat/completions (non-streaming)",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_streaming_chat(
    client: httpx.AsyncClient, model: str, messages: list[dict]
) -> CheckResult:
    """Check POST /v1/chat/completions (streaming) with Codexify SSE parsing."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
            timeout=300.0,
        )
        content_type = resp.headers.get("content-type", "")
        if resp.status_code != 200:
            return CheckResult(
                name="POST /v1/chat/completions (streaming)",
                status=CheckStatus.FAIL,
                detail=f"status={resp.status_code}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        visible_text, chunk_count, has_done = parse_codexify_sse(resp.text)
        is_sse = "text/event-stream" in content_type

        issues: list[str] = []
        if not is_sse:
            issues.append("missing text/event-stream content-type")
        if not has_done:
            issues.append("missing [DONE] sentinel")
        if chunk_count == 0:
            issues.append("no chunks parsed")
        if not visible_text.strip():
            issues.append("no visible text")

        status = CheckStatus.PASS if not issues else CheckStatus.FAIL
        return CheckResult(
            name="POST /v1/chat/completions (streaming)",
            status=status,
            detail=(
                f"chunks={chunk_count} done={has_done} text_len={len(visible_text)}"
                + (f" issues={issues}" if issues else "")
            ),
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="POST /v1/chat/completions (streaming)",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


async def _check_codexify_sse_compat(
    client: httpx.AsyncClient, model: str, messages: list[dict]
) -> CheckResult:
    """Check Codexify-specific SSE compatibility: chunk shape, field presence."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
            timeout=300.0,
        )
        if resp.status_code != 200:
            return CheckResult(
                name="Codexify SSE compat",
                status=CheckStatus.FAIL,
                detail=f"status={resp.status_code}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Parse chunks and validate each.
        issues: list[str] = []
        chunk_count = 0
        first_chunk_has_role = False
        final_chunk_has_finish = False

        for line in resp.text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                issues.append("unparseable JSON chunk")
                continue

            chunk_count += 1

            # Validate required top-level fields.
            for field in ("id", "object", "created", "model", "choices"):
                if field not in chunk:
                    issues.append(f"chunk {chunk_count} missing field '{field}'")

            if chunk.get("object") != "chat.completion.chunk":
                issues.append(f"chunk {chunk_count} object={chunk.get('object')}")

            choices = chunk.get("choices", [])
            if not choices:
                issues.append(f"chunk {chunk_count} has empty choices")
                continue

            delta = choices[0].get("delta", {})
            finish = choices[0].get("finish_reason")

            if chunk_count == 1:
                if delta.get("role") == "assistant":
                    first_chunk_has_role = True

            if finish == "stop":
                final_chunk_has_finish = True

        if not first_chunk_has_role:
            issues.append("first chunk missing role=assistant")
        if not final_chunk_has_finish:
            issues.append("no chunk with finish_reason=stop")

        status = CheckStatus.PASS if not issues else CheckStatus.FAIL
        return CheckResult(
            name="Codexify SSE compat",
            status=status,
            detail=f"chunks={chunk_count} role={first_chunk_has_role} finish={final_chunk_has_finish}"
                    + (f" issues={issues}" if issues else ""),
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="Codexify SSE compat",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


# ── Vision semantic check ──────────────────────────────────────────────────


async def _check_vision_semantic(
    client: httpx.AsyncClient, model: str, messages: list[dict],
    expect_texts: list[str] | None = None,
) -> CheckResult | None:
    """Check that the vision model's non-streaming response contains expected terms.

    Returns None if no expected terms are provided.
    """
    if not expect_texts:
        return None

    t0 = time.monotonic()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": messages, "stream": False},
            timeout=300.0,
        )
        body = resp.json()
        if resp.status_code != 200:
            return CheckResult(
                name="Vision semantic check",
                status=CheckStatus.FAIL,
                detail=f"HTTP {resp.status_code}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        choices = body.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        content_lower = content.lower()

        matched = [t for t in expect_texts if t.lower() in content_lower]
        missing = [t for t in expect_texts if t.lower() not in content_lower]

        if missing:
            return CheckResult(
                name="Vision semantic check",
                status=CheckStatus.FAIL,
                detail=f"matched={matched} missing={missing} answer={content[:120]!r}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        return CheckResult(
            name="Vision semantic check",
            status=CheckStatus.PASS,
            detail=f"matched={matched} answer={content[:120]!r}",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
    except Exception as exc:
        return CheckResult(
            name="Vision semantic check",
            status=CheckStatus.FAIL,
            detail=str(exc),
            duration_ms=(time.monotonic() - t0) * 1000,
        )


# ── Concurrent streaming smoke test ─────────────────────────────────────────


async def _run_single_concurrent_stream(
    client: httpx.AsyncClient, req_id: int, model: str, messages: list[dict]
) -> ConcurrentStreamResult:
    """Run one concurrent streaming request and collect timing."""
    result = ConcurrentStreamResult(request_id=req_id)
    result.accepted_at = time.monotonic()

    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
            timeout=300.0,
        ) as resp:
            if resp.status_code != 200:
                result.status = f"HTTP {resp.status_code}"
                return result

            first_token = True
            tokens: list[str] = []
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break

                if first_token:
                    result.first_token_at = time.monotonic()
                    result.ttft_ms = (result.first_token_at - result.accepted_at) * 1000
                    first_token = False

                try:
                    chunk = json.loads(payload)
                    result.chunk_count += 1
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            tokens.append(content)
                except json.JSONDecodeError:
                    pass

            result.completed_at = time.monotonic()
            result.total_ms = (result.completed_at - result.accepted_at) * 1000
            result.visible_text = "".join(tokens)
            result.status = "ok"

    except Exception as exc:
        result.status = f"error: {exc}"
        result.completed_at = time.monotonic()
        result.total_ms = (result.completed_at - result.accepted_at) * 1000

    return result


async def _check_concurrent_streaming(
    client: httpx.AsyncClient, model: str, concurrency: int
) -> CheckResult:
    """Run concurrent streaming requests and validate isolation."""
    t0 = time.monotonic()
    messages = [{"role": "user", "content": f"Say hello in one short sentence. (request #{i})"} for i in range(concurrency)]

    # Use separate clients per request to simulate true concurrency.
    async def _run_one(i: int):
        # Create a fresh client for each concurrent request.
        async with httpx.AsyncClient(base_url=str(client.base_url), timeout=300.0) as c:
            return await _run_single_concurrent_stream(c, i, model, [{"role": "user", "content": messages[i]["content"]}])

    tasks = [_run_one(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)

    # Validate results.
    ok_count = sum(1 for r in results if r.status == "ok")
    overloaded_count = sum(1 for r in results if r.status.startswith("HTTP 429"))
    stuck = sum(1 for r in results if r.status not in ("ok",) and not r.status.startswith("HTTP 429"))
    total_ttft = sum(r.ttft_ms for r in results if r.ttft_ms > 0)
    avg_ttft = total_ttft / max(ok_count, 1)
    empty = sum(1 for r in results if r.status == "ok" and not r.visible_text.strip())

    issues: list[str] = []
    if stuck > 0:
        issues.append(f"{stuck}/{concurrency} stuck")
    if overloaded_count > 0:
        issues.append(f"{overloaded_count}/{concurrency} overloaded (429)")
    if empty > 0:
        issues.append(f"{empty}/{concurrency} empty output")
    if ok_count == 0 and stuck > 0:
        issues.append("all requests failed")

    # Only FAIL if there are truly stuck requests.
    # Overloaded (429) is expected behavior at capacity.
    status = CheckStatus.PASS if not issues or all("overloaded" in i for i in issues) else CheckStatus.FAIL
    detail = (
        f"ok={ok_count}/{concurrency} avg_ttft={avg_ttft:.0f}ms "
        f"stuck={stuck} overloaded={overloaded_count} empty={empty}"
    )
    if issues:
        detail += f" issues={issues}"

    return CheckResult(
        name=f"Concurrent streaming (x{concurrency})",
        status=status,
        detail=detail,
        duration_ms=(time.monotonic() - t0) * 1000,
    )


# ── Main validation runner ──────────────────────────────────────────────────


async def validate_runtime(
    whooshd_url: str,
    model: str,
    run_streaming: bool = True,
    run_non_streaming: bool = True,
    concurrency: int = 0,
    runtimes: list[str] | None = None,
    expect_texts: list[str] | None = None,
    vision_messages: list[dict] | None = None,
    expect_runtime: str | None = None,
    expect_model: str | None = None,
    force: bool = False,
) -> tuple[list[CheckResult], int]:
    """Run the full validation suite against the configured Whoosh'd instance.

    Returns (results, exit_code).
    """
    all_results: list[CheckResult] = []
    blocked = False

    # ── Step 0: Dependency detection ──────────────────────────────────
    if runtimes:
        dep_results = detect_dependencies(runtimes)
        all_results.extend(dep_results)
        if any(r.status == CheckStatus.BLOCKED for r in dep_results):
            blocked = True

    # ── Step 0.5: Runtime identity pre-check ─────────────────────────
    base_url = whooshd_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as pre_client:
        try:
            hr = await pre_client.get("/health/runtime")
            if hr.status_code == 200:
                hr_body = hr.json()
                session = hr_body.get("session", {})
                reported_kinds = list(hr_body.get("runtimes", {}).keys())

                # Check expected runtime kind.
                if expect_runtime:
                    expected_kind = _runtime_cli_to_kind(expect_runtime)
                    if expected_kind and expected_kind not in reported_kinds:
                        msg = (
                            f"Expected runtime '{expect_runtime}' (kind={expected_kind}) "
                            f"but server reports runtimes: {reported_kinds}. "
                            f"Possible stale Whoosh'd process on port {whooshd_url.split(':')[-1]}. "
                            f"Session: pid={session.get('pid','?')}"
                        )
                        all_results.append(CheckResult("Runtime identity check", CheckStatus.FAIL, msg))
                        if not force:
                            return all_results, EXIT_FAIL

                # Check that stub-model is not the only model when a real runtime is expected.
                non_stub = [k for k in reported_kinds if k != "stub"]
                if expect_runtime and not non_stub:
                    msg = (
                        f"Expected runtime '{expect_runtime}' but server has no non-stub runtimes. "
                        f"Server reports: {reported_kinds}. Possible stale stub-only process."
                    )
                    all_results.append(CheckResult("Runtime identity check", CheckStatus.FAIL, msg))
                    if not force:
                        return all_results, EXIT_FAIL

                # Record starting session_id for later verification.
                start_session_id = session.get("session_id", "")

        except Exception:
            start_session_id = ""
            pass  # Will be caught by /health check later.

    # ── Step 1-5: HTTP checks ────────────────────────────────────────
    base_url = whooshd_url.rstrip("/")
    messages = vision_messages or [{"role": "user", "content": "Say hello in exactly one sentence."}]

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # Basic health checks.
        all_results.append(await _check_health(client))
        all_results.append(await _check_health_runtime(client))
        all_results.append(await _check_ready(client))

        # Model inventory.
        all_results.append(await _check_models(client))
        all_results.append(await _check_ollama_tags(client))

        # Non-streaming chat.
        if run_non_streaming:
            all_results.append(await _check_non_streaming_chat(client, model, messages))

        # Streaming chat.
        if run_streaming:
            all_results.append(await _check_streaming_chat(client, model, messages))
            all_results.append(await _check_codexify_sse_compat(client, model, messages))

        # Vision semantic check (if expect_texts provided).
        semantic = await _check_vision_semantic(client, model, messages, expect_texts)
        if semantic is not None:
            all_results.append(semantic)

        # Concurrent streaming.
        if concurrency > 1 and run_streaming:
            all_results.append(await _check_concurrent_streaming(client, model, concurrency))

    # ── Session stability check ─────────────────────────────────────
    if start_session_id:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as sc:
                resp = await sc.get("/health/runtime")
                if resp.status_code == 200:
                    end_session = resp.json().get("session", {})
                    end_session_id = end_session.get("session_id", "")
                    if end_session_id and end_session_id != start_session_id:
                        msg = (
                            f"Whoosh'd session changed during validation "
                            f"(was {start_session_id}, now {end_session_id}). "
                            f"Server may have restarted or requests hit a different process."
                        )
                        all_results.append(CheckResult("Session stability", CheckStatus.FAIL, msg))
                    else:
                        all_results.append(CheckResult("Session stability", CheckStatus.PASS,
                                                       f"session_id={start_session_id} unchanged"))
        except Exception:
            pass  # Session check is best-effort.

    # ── Determine exit code ──────────────────────────────────────────
    if blocked:
        exit_code = EXIT_BLOCKED
    elif any(r.status == CheckStatus.FAIL for r in all_results):
        exit_code = EXIT_FAIL
    else:
        exit_code = EXIT_OK

    return all_results, exit_code


# ── Output formatters ───────────────────────────────────────────────────────


def print_table(results: list[CheckResult]) -> None:
    """Print a human-readable table of results."""
    # Column widths.
    name_width = max(len(r.name) for r in results) + 2
    status_width = 10
    dur_width = 10

    # Header.
    print()
    print(f"{'Check':<{name_width}} {'Status':<{status_width}} {'Duration':<{dur_width}} Detail")
    print("-" * (name_width + status_width + dur_width + 40))

    for r in results:
        status_str = r.status.value.upper()
        dur_str = f"{r.duration_ms:.0f}ms" if r.duration_ms else "-"
        print(f"{r.name:<{name_width}} {status_str:<{status_width}} {dur_str:<{dur_width}} {r.detail}")

    # Summary.
    passed = sum(1 for r in results if r.status == CheckStatus.PASS)
    failed = sum(1 for r in results if r.status == CheckStatus.FAIL)
    blocked = sum(1 for r in results if r.status == CheckStatus.BLOCKED)
    skipped = sum(1 for r in results if r.status == CheckStatus.SKIP)

    print()
    summary_parts = [f"{passed} passed"]
    if failed:
        summary_parts.append(f"{failed} failed")
    if blocked:
        summary_parts.append(f"{blocked} blocked")
    if skipped:
        summary_parts.append(f"{skipped} skipped")
    print(f"Summary: {', '.join(summary_parts)}")


def _runtime_cli_to_kind(cli_name: str) -> str | None:
    """Map CLI runtime name to internal RuntimeKind value."""
    mapping = {
        "llama-cpp": "llama_cpp",
        "mlx-lm-server": "mlx_lm_server",
        "mlx-vlm": "mlx_vlm",
    }
    return mapping.get(cli_name)


async def _diagnose_port(port: int) -> int:
    """Diagnose whether a Whoosh'd process is running on *port*."""
    url = f"http://127.0.0.1:{port}"
    print(f"=== Whoosh'd Port Diagnostic: {url} ===\n")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check /health
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    body = resp.json()
                    print(f"/health: OK (runner={body.get('runner','?')} v{body.get('version','?')})")
                    print(f"  lifecycle: {body.get('model_lifecycle','?')}")
                else:
                    print(f"/health: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"/health: unreachable ({exc})")
                print(f"\nPort {port} does not appear to be running Whoosh'd.")
                return 2

            # Check /health/runtime
            try:
                resp = await client.get(f"{url}/health/runtime")
                if resp.status_code == 200:
                    body = resp.json()
                    session = body.get("session", {})
                    print(f"\n/health/runtime: OK")
                    print(f"  session pid: {session.get('pid','?')}")
                    print(f"  session id:  {session.get('session_id','?')}")
                    runtimes = body.get("runtimes", {})
                    print(f"  registered kinds: {list(runtimes.keys())}")
                    for kind, rt in runtimes.items():
                        print(f"    {kind}: state={rt.get('state','?')} "
                              f"active_model={rt.get('active_model','?')} "
                              f"configured_model={rt.get('configured_model','?')}")
                else:
                    print(f"/health/runtime: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"/health/runtime: unreachable ({exc})")

            # Check /v1/models
            try:
                resp = await client.get(f"{url}/v1/models")
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    print(f"\n/v1/models: {len(data)} models")
                    for m in data:
                        print(f"  {m['id']}")
                else:
                    print(f"/v1/models: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"/v1/models: unreachable ({exc})")

    except Exception as exc:
        print(f"Diagnostic failed: {exc}")
        return 2

    return 0


def print_json(results: list[CheckResult], exit_code: int) -> None:
    """Print results as JSON."""
    output = {
        "exit_code": exit_code,
        "results": [
            {
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "duration_ms": round(r.duration_ms, 1),
            }
            for r in results
        ],
    }
    print(json.dumps(output, indent=2))


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Whoosh'd Local Runtime Validation Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/validate_local_runtimes.py --runtime llama-cpp
  python scripts/validate_local_runtimes.py --runtime both --concurrency 2
  python scripts/validate_local_runtimes.py --runtime both --json
        """,
    )
    parser.add_argument(
        "--runtime",
        choices=["llama-cpp", "mlx-lm-server", "mlx-vlm", "both"],
        default="both",
        help="Which runtime(s) to validate (default: both)",
    )
    parser.add_argument(
        "--whooshd-url",
        default="http://127.0.0.1:8000",
        help="Whoosh'd server base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID to use for chat completions. If unset, the first model from /v1/models is used.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP request timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=0,
        help="Number of concurrent streaming requests (0 = skip, default: 0)",
    )
    parser.add_argument(
        "--stream",
        dest="run_streaming",
        action="store_true",
        default=True,
        help="Run streaming chat tests (default: True)",
    )
    parser.add_argument(
        "--no-stream",
        dest="run_streaming",
        action="store_false",
        help="Skip streaming chat tests",
    )
    parser.add_argument(
        "--non-stream",
        dest="run_non_streaming",
        action="store_true",
        default=True,
        help="Run non-streaming chat tests (default: True)",
    )
    parser.add_argument(
        "--no-non-stream",
        dest="run_non_streaming",
        action="store_false",
        help="Skip non-streaming chat tests",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON instead of a table",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Path to an image file for vision validation (base64-encoded in the request).",
    )
    parser.add_argument(
        "--vision-prompt",
        default="Describe this image in one short sentence.",
        help="Prompt to use with --image for vision validation.",
    )
    parser.add_argument(
        "--expect-text",
        action="append",
        default=None,
        dest="expect_texts",
        help="Expected text term in the vision model response. May be specified multiple times.",
    )
    parser.add_argument(
        "--expect-runtime",
        default=None,
        choices=["llama-cpp", "mlx-lm-server", "mlx-vlm"],
        help="Expected runtime kind. Validation fails if the server reports a different runtime.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Continue validation even if runtime identity checks fail.",
    )
    parser.add_argument(
        "--diagnose-port",
        type=int,
        default=None,
        help="Diagnose a port for running Whoosh'd processes (standalone mode).",
    )

    args = parser.parse_args()

    # Determine runtime list.
    if args.runtime == "both":
        runtimes = ["llama-cpp", "mlx-lm-server", "mlx-vlm"]
    else:
        runtimes = [args.runtime]

    # Handle --diagnose-port standalone mode.
    if args.diagnose_port is not None:
        return asyncio.run(_diagnose_port(args.diagnose_port))

    # Auto-detect model if not specified.
    model = args.model
    if model is None:
        # Try to discover from /v1/models.
        try:
            async def _discover():
                async with httpx.AsyncClient(base_url=args.whooshd_url, timeout=10.0) as client:
                    resp = await client.get("/v1/models")
                    data = resp.json().get("data", [])
                    if data:
                        return data[0]["id"]
                return "stub-model"
            model = asyncio.run(_discover())
        except Exception:
            model = "stub-model"

    # Build vision messages if --image is provided.
    vision_messages = None
    if args.image:
        import base64 as _b64
        with open(args.image, "rb") as f:
            img_b64 = _b64.b64encode(f.read()).decode("ascii")
        vision_messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": args.vision_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }]

    # Run validation.
    results, exit_code = asyncio.run(
        validate_runtime(
            whooshd_url=args.whooshd_url,
            model=model,
            run_streaming=args.run_streaming,
            run_non_streaming=args.run_non_streaming,
            concurrency=args.concurrency,
            runtimes=runtimes,
            expect_texts=args.expect_texts,
            vision_messages=vision_messages,
            expect_runtime=args.expect_runtime,
            force=args.force,
        )
    )

    # Output.
    if args.json:
        print_json(results, exit_code)
    else:
        print_table(results)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

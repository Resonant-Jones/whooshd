"""Benchmark runner — sends concurrent requests and aggregates results.

External harness: hits the server over HTTP, never touches internals.
Measures latency, TTFT, success/failure/rejection counts.
Safe output by default — no prompts, no generated text, no tracebacks.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from typing import Optional

import httpx

from whooshd.bench.contracts import BenchmarkSummary, RequestBenchmarkResult
from whooshd.compat.codexify_probe import reconstruct_assistant_text
from whooshd.log_safety import exception_metadata


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Simple percentile (nearest-rank).  Returns None for empty lists."""
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(math.ceil(pct / 100.0 * len(sorted_vals))) - 1))
    return sorted_vals[idx]


async def _run_single_non_streaming(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    index: int,
) -> RequestBenchmarkResult:
    t0 = time.time()
    try:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        t1 = time.time()
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        ok = 200 <= resp.status_code < 300
        visible_chars = len(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
        return RequestBenchmarkResult(
            request_index=index,
            ok=ok,
            status_code=resp.status_code,
            stream=False,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            visible_chars=visible_chars,
            error_code=body.get("code") if not ok else None,
            error_message=body.get("message") if not ok else None,
        )
    except Exception as exc:
        t1 = time.time()
        return RequestBenchmarkResult(
            request_index=index,
            ok=False,
            status_code=None,
            stream=False,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            error_message=exception_metadata(exc),
        )


async def _run_single_streaming(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    index: int,
) -> RequestBenchmarkResult:
    t0 = time.time()
    ttft: Optional[float] = None
    chunk_count = 0
    raw_lines: list[str] = []
    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        ) as resp:
            async for line in resp.aiter_lines():
                raw_lines.append(line)
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk_count += 1
                if ttft is None:
                    try:
                        chunk = json.loads(payload)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            ttft = time.time() - t0
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
            status_code = resp.status_code
        t1 = time.time()
        body_text = "\n".join(raw_lines)
        visible_text = reconstruct_assistant_text(body_text)
        ok = 200 <= status_code < 300
        return RequestBenchmarkResult(
            request_index=index,
            ok=ok,
            status_code=status_code,
            stream=True,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            ttft_ms=ttft * 1000 if ttft is not None else None,
            chunks=chunk_count,
            visible_chars=len(visible_text),
            error_code=None if ok else _extract_error_code(body_text),
            error_message=None,
        )
    except Exception as exc:
        t1 = time.time()
        return RequestBenchmarkResult(
            request_index=index,
            ok=False,
            status_code=None,
            stream=True,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            error_message=exception_metadata(exc),
        )


def _extract_error_code(body_text: str) -> Optional[str]:
    try:
        # Try to parse JSON error body.
        body = json.loads(body_text)
        return body.get("code")
    except (json.JSONDecodeError, AttributeError):
        return None


async def run_benchmark(
    *,
    base_url: str = "http://localhost:8000",
    model: str = "stub-model",
    concurrency: int = 1,
    total_requests: int = 10,
    stream: bool = False,
    prompt: str = "Say hello from Whooshd.",
    max_tokens: int = 64,
    timeout_seconds: float = 120.0,
) -> BenchmarkSummary:
    """Run a benchmark against a Whoosh'd server.

    Args:
        base_url: Target server URL.
        model: Model ID for chat completion requests.
        concurrency: Maximum concurrent in-flight requests.
        total_requests: Total requests to send.
        stream: Whether to use streaming chat completions.
        prompt: User prompt text.
        max_tokens: Maximum tokens to generate.
        timeout_seconds: Per-request timeout.

    Returns a ``BenchmarkSummary`` with aggregate results.
    """
    semaphore = asyncio.Semaphore(concurrency)
    errors: list[str] = []

    runner = _run_single_streaming if stream else _run_single_non_streaming

    wall_t0 = time.time()

    async def _bounded_run(index: int) -> RequestBenchmarkResult:
        async with semaphore:
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
                return await runner(client, model, prompt, max_tokens, timeout_seconds, index)

    tasks = [asyncio.create_task(_bounded_run(i)) for i in range(total_requests)]
    results = await asyncio.gather(*tasks)
    wall_t1 = time.time()

    succeeded = [r for r in results if r.ok]
    failed_5xx = [r for r in results if not r.ok and r.status_code is not None and 500 <= r.status_code < 600]
    rejected_429 = [r for r in results if r.status_code == 429]
    latencies = [r.total_ms for r in results]

    ttft_values = [r.ttft_ms for r in results if r.ttft_ms is not None]
    visible_chars = sum(r.visible_chars or 0 for r in results)

    total_wall = (wall_t1 - wall_t0) * 1000
    chars_per_sec = visible_chars / (total_wall / 1000) if total_wall > 0 else None

    return BenchmarkSummary(
        ok=len(succeeded) == total_requests,
        base_url=base_url,
        model=model,
        stream=stream,
        concurrency=concurrency,
        total_requests=total_requests,
        succeeded=len(succeeded),
        failed=len(failed_5xx),
        rejected=len(rejected_429),
        total_wall_ms=total_wall,
        min_latency_ms=min(latencies) if latencies else None,
        max_latency_ms=max(latencies) if latencies else None,
        mean_latency_ms=statistics.mean(latencies) if latencies else None,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        mean_ttft_ms=statistics.mean(ttft_values) if ttft_values else None,
        p50_ttft_ms=_percentile(ttft_values, 50) if ttft_values else None,
        p95_ttft_ms=_percentile(ttft_values, 95) if ttft_values else None,
        total_visible_chars=visible_chars,
        chars_per_second=chars_per_sec,
        errors=errors,
    )


# ── CLI entry point ─────────────────────────────────────────────────────────


def _format_summary_text(summary: BenchmarkSummary) -> str:
    lines = [
        "Whoosh'd Benchmark",
        "",
        f"base_url: {summary.base_url}",
        f"model: {summary.model}",
        f"stream: {summary.stream}",
        f"concurrency: {summary.concurrency}",
        f"requests: {summary.total_requests}",
        "",
        f"succeeded: {summary.succeeded}",
        f"failed: {summary.failed}",
        f"rejected: {summary.rejected}",
        "",
    ]
    if summary.mean_latency_ms is not None:
        lines.append("latency_ms:")
        lines.append(f"  mean: {summary.mean_latency_ms:.1f}")
        if summary.p50_latency_ms is not None:
            lines.append(f"  p50: {summary.p50_latency_ms:.1f}")
        if summary.p95_latency_ms is not None:
            lines.append(f"  p95: {summary.p95_latency_ms:.1f}")
        lines.append("")
    if summary.mean_ttft_ms is not None:
        lines.append("ttft_ms:")
        lines.append(f"  mean: {summary.mean_ttft_ms:.1f}")
        if summary.p50_ttft_ms is not None:
            lines.append(f"  p50: {summary.p50_ttft_ms:.1f}")
        if summary.p95_ttft_ms is not None:
            lines.append(f"  p95: {summary.p95_ttft_ms:.1f}")
        lines.append("")
    if summary.chars_per_second is not None:
        lines.append(f"visible_chars: {summary.total_visible_chars}")
        lines.append(f"chars_per_second: {summary.chars_per_second:.1f}")
        lines.append("")
    if summary.errors:
        lines.append(f"errors: {len(summary.errors)}")
        for e in summary.errors:
            lines.append(f"  - {e}")
        lines.append("")
    lines.append(f"result: {'pass' if summary.ok else 'fail'}")
    return "\n".join(lines)


async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Whoosh'd Benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--model", default="stub-model")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--stream", action="store_true", default=False)
    parser.add_argument("--prompt", default="Say hello from Whooshd.")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--json", action="store_true", default=False)
    args = parser.parse_args()

    summary = await run_benchmark(
        base_url=args.base_url,
        model=args.model,
        concurrency=args.concurrency,
        total_requests=args.requests,
        stream=args.stream,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout_seconds,
    )

    if args.json:
        print(summary.model_dump_json(indent=2))
    else:
        print(_format_summary_text(summary))

    raise SystemExit(0 if summary.ok else 1)


if __name__ == "__main__":
    asyncio.run(_main())

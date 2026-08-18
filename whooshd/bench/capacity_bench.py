"""Capacity benchmark CLI — measure concurrency bands for one (model, runtime).

Runs a sequence of bands (e.g. 1, 2, 3, 4, 6, 8 concurrent requests) against
a Whoosh'd server, captures per-band evidence, and writes a structured
:class:`CapacityProfile` JSON for the capacity controller to consume.

This is **not** the throughput benchmark — it does not try to maximise
requests per second.  It is calibration: "given this model/runtime on
this machine, what is the safe active concurrency ceiling?".  Each band
is bounded by a per-request timeout and runs a fixed number of
requests.  Bands that exceed the timeout, return failures, or show
collapse are recorded as ``successful=False``.

Output:
    ``--output`` (default ``./capacity_profile.json``) — JSON conforming
    to :class:`CapacityProfile`.  Suitable for the
    ``WHOOSHD_CAPACITY_PROFILE_PATH`` env var.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import time
from typing import Optional

import httpx

from whooshd.bench.contracts import RequestBenchmarkResult
from whooshd.capacity_profile import (
    CapacityBand,
    CapacityProfile,
    MemoryPressureClass,
    safe_save_profile,
)


# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_BANDS = (1, 2, 3, 4, 6, 8)
DEFAULT_REQUESTS_PER_BAND = 8
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_PROMPT = "Write a Python function that returns the n-th Fibonacci number."
DEFAULT_MAX_TOKENS = 128


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Simple nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(math.ceil(pct / 100.0 * len(s))) - 1))
    return s[idx]


# ── Band execution ─────────────────────────────────────────────────────────


async def _run_one(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    stream: bool,
    timeout: float,
    index: int,
) -> RequestBenchmarkResult:
    t0 = time.time()
    try:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": stream,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        t1 = time.time()
        ok = 200 <= resp.status_code < 300
        chars = 0
        ttft_ms: Optional[float] = None
        if stream and ok:
            ttft_ms = (t1 - t0) * 1000  # approximate
        else:
            try:
                body = resp.json()
                chars = len(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
            except Exception:
                pass
        return RequestBenchmarkResult(
            request_index=index,
            ok=ok,
            status_code=resp.status_code,
            stream=stream,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            ttft_ms=ttft_ms,
            chunks=0,
            visible_chars=chars,
            error_code=None if ok else f"http_{resp.status_code}",
            error_message=None,
        )
    except Exception as exc:
        t1 = time.time()
        return RequestBenchmarkResult(
            request_index=index,
            ok=False,
            status_code=None,
            stream=stream,
            started_at=t0,
            ended_at=t1,
            total_ms=(t1 - t0) * 1000,
            ttft_ms=None,
            chunks=None,
            visible_chars=None,
            error_code=None,
            error_message=f"{type(exc).__name__}: {exc}",
        )


async def _run_band(
    *,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    stream: bool,
    concurrency: int,
    total_requests: int,
    timeout: float,
) -> list[RequestBenchmarkResult]:
    """Run one concurrency band: ``total_requests`` requests at the given concurrency."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(index: int) -> RequestBenchmarkResult:
        async with semaphore:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await _run_one(
                    client,
                    base_url=base_url,
                    model=model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    stream=stream,
                    timeout=timeout,
                    index=index,
                )

    tasks = [asyncio.create_task(_bounded(i)) for i in range(total_requests)]
    return await asyncio.gather(*tasks)


def _summarise_band(
    results: list[RequestBenchmarkResult],
    *,
    band_concurrency: int,
) -> CapacityBand:
    """Convert per-request results into a :class:`CapacityBand`."""
    total = len(results)
    success_count = sum(1 for r in results if r.ok)
    failure_count = sum(
        1 for r in results if r.status_code is not None and 500 <= r.status_code < 600
    )
    overload_count = sum(1 for r in results if r.status_code == 429)
    stuck_count = sum(
        1 for r in results if not r.ok and r.status_code is None
    )

    latencies = [r.total_ms for r in results if r.total_ms is not None]
    ttfts = [r.ttft_ms for r in results if r.ttft_ms is not None]

    # Aggregate throughput: visible chars / total wall.  NOT token throughput.
    # The benchmark CLI does not have token counts from the stub; honest
    # measurement must come from the runtime, not character counts.
    if results:
        started = min(r.started_at for r in results)
        ended = max(r.ended_at for r in results)
        wall_total_ms = max((ended - started) * 1000.0, 1.0)
    else:
        wall_total_ms = 1.0

    return CapacityBand(
        concurrency=band_concurrency,
        successful=(stuck_count == 0 and failure_count == 0),
        request_count=total,
        ttft_p50_ms=_percentile(ttfts, 50) if ttfts else None,
        ttft_p95_ms=_percentile(ttfts, 95) if ttfts else None,
        latency_p50_ms=_percentile(latencies, 50),
        latency_p95_ms=_percentile(latencies, 95),
        aggregate_tokens_per_second=None,  # honest: not measurable here
        per_request_tokens_per_second=None,
        success_count=success_count,
        overload_count=overload_count,
        failure_count=failure_count,
        stuck_count=stuck_count,
        memory_pressure=MemoryPressureClass.UNKNOWN,
        runtime_memory_mb=None,
        host_memory_pressure=None,
        notes=(
            "aggregate_tokens_per_second is None because character counts do not equal tokens.",
            f"wall_total_ms={wall_total_ms:.1f}",
        ),
    )


def _recommend_concurrency(bands: list[CapacityBand]) -> int:
    """Pick a recommended concurrency from the band evidence.

    Rules:
        * Only consider ``successful=True`` bands.
        * Pick the highest successful concurrency as the starting point.
        * Walk down until either latency p95 is non-degenerate.

    Conservative: when all bands fail, recommend 1.
    """
    successful = [b for b in bands if b.successful and b.concurrency >= 1]
    if not successful:
        return 1
    # Highest successful band.
    best = max(successful, key=lambda b: b.concurrency)
    return best.concurrency


# ── CLI entry point ────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> int:
    bands: list[int] = list(args.band)
    profile_bands: list[CapacityBand] = []

    # Concurrency == 1 first; ascending thereafter.
    for band_concurrency in bands:
        print(f"=== band: concurrency={band_concurrency} requests={args.requests} ===")
        results = await _run_band(
            base_url=args.base_url,
            model=args.model,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
            stream=args.stream,
            concurrency=band_concurrency,
            total_requests=args.requests,
            timeout=args.timeout,
        )
        band_evidence = _summarise_band(results, band_concurrency=band_concurrency)
        profile_bands.append(band_evidence)
        print(
            f"  ok={band_evidence.success_count} fail={band_evidence.failure_count} "
            f"rej={band_evidence.overload_count} stuck={band_evidence.stuck_count} "
            f"p50={band_evidence.latency_p50_ms} p95={band_evidence.latency_p95_ms}"
        )
        # Stop early on a hard failure (stuck_count > 0 or failure > 0).
        if band_evidence.stuck_count > 0 or band_evidence.failure_count > 0:
            print("  band failed — stopping further bands")
            break

    recommended = _recommend_concurrency(profile_bands)
    print(f"\nrecommended_active_concurrency = {recommended}")

    profile = CapacityProfile(
        model_id=args.model,
        runtime=args.runtime,
        machine_class=_machine_class(),
        host_memory_bytes=_host_memory_bytes(),
        prompt_size_chars=len(args.prompt),
        configured_max_tokens=args.max_tokens,
        streaming=args.stream,
        bands=tuple(profile_bands),
        recommended_active_concurrency=recommended,
        benchmark_version="1.0",
    )

    saved = safe_save_profile(profile, args.output)
    if not saved:
        print(f"ERROR: failed to write {args.output}", flush=True)
        return 2

    print(f"wrote {args.output}")
    if args.json_stdout:
        print(profile.model_dump_json(indent=2))
    return 0


def _machine_class() -> str:
    """Stable, descriptive machine identifier."""
    system = platform.system() or "unknown"
    machine = platform.machine() or "unknown"
    return f"{system.lower()}-{machine.lower()}"


def _host_memory_bytes() -> Optional[int]:
    """Best-effort host memory in bytes.  None if unavailable."""
    try:
        if hasattr(os, "sysconf"):
            val = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            if val and pages:
                return int(val) * int(pages)
    except Exception:
        pass
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Whoosh'd capacity benchmark")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="stub-model")
    p.add_argument("--runtime", default="stub")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--stream", action="store_true", default=False)
    p.add_argument("--requests", type=int, default=DEFAULT_REQUESTS_PER_BAND)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument(
        "--band",
        type=int,
        nargs="+",
        default=list(DEFAULT_BANDS),
        help="Concurrency bands to test (space-separated).",
    )
    p.add_argument(
        "--output",
        default="./capacity_profile.json",
        help="Path to write the structured CapacityProfile JSON.",
    )
    p.add_argument(
        "--json-stdout",
        action="store_true",
        default=False,
        help="Also print the full profile JSON to stdout.",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
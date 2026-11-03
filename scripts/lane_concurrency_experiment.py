"""Isolated two-request concurrency experiment (roadmap follow-up).

Runs a SEPARATE Whoosh'd broker instance with
``WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS=2`` against the same 12B runtime,
without touching the working deployment on port 8000.

Measures, per experiment:
  * aggregate and per-request token throughput
  * individual TTFT
  * host swap activity (the thrash signal)
  * host memory pressure
  * failures and overloads
  * lifecycle cleanup after the run

Stops and reports FAILURE on evidence of instability: any non-2xx response
that is not an overload, any request error, or swap pages *out* during the
run (page-out is the thrash signal; page-in alone is normal warm-up).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time

import httpx

BASE = os.environ.get("EXPERIMENT_BASE_URL", "http://127.0.0.1:18940")
MODEL = os.environ.get("EXPERIMENT_MODEL", "local-chat")
UPSTREAM = os.environ.get("EXPERIMENT_UPSTREAM_URL", "http://127.0.0.1:8082")
UPSTREAM_MODEL = os.environ.get(
    "EXPERIMENT_UPSTREAM_MODEL",
    "/Volumes/Dev_SSD/whooshd/model-weights/hub/models--mlx-community--gemma-4-12B-it-qat-4bit",
)
PROMPT_CHARS = int(os.environ.get("EXPERIMENT_PROMPT_CHARS", "2048"))
MAX_TOKENS = int(os.environ.get("EXPERIMENT_MAX_TOKENS", "128"))
TIMEOUT = float(os.environ.get("EXPERIMENT_TIMEOUT", "300"))

PROMPT = (
    "You are a careful assistant. Summarise the following operational note in "
    "three short sentences, then state the single most important action item. "
    "The note describes a multi-tenant inference broker that admits requests "
    "against a bounded queue, schedules them FIFO, and records lifecycle "
    "metadata for every request without ever persisting prompt content. "
) * (PROMPT_CHARS // 400 + 1)


def swap_counters() -> dict[str, int] | None:
    try:
        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except Exception:
        return None
    counters: dict[str, int] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("swapins", "swapouts"):
            try:
                counters[key] = int(value.strip().rstrip("."))
            except ValueError:
                pass
    return counters or None


def mem_pressure() -> str:
    try:
        out = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=15, check=False
        ).stdout
        for line in out.splitlines():
            if "percentage" in line.lower():
                return line.strip()
    except Exception:
        pass
    return "unavailable"


async def one_request(client: httpx.AsyncClient, index: int) -> dict:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT[:PROMPT_CHARS]}],
        "max_tokens": MAX_TOKENS,
    }
    t0 = time.time()
    record: dict = {"index": index}
    try:
        resp = await client.post(f"{BASE}/v1/chat/completions", json=payload, timeout=TIMEOUT)
        record["status"] = resp.status_code
        record["latency_ms"] = round((time.time() - t0) * 1000, 1)
        if 200 <= resp.status_code < 300:
            body = resp.json()
            usage = body.get("usage") or {}
            record["completion_tokens"] = usage.get("completion_tokens")
            record["prompt_tokens"] = usage.get("prompt_tokens")
            record["ok"] = True
        else:
            record["ok"] = False
            record["error"] = (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text)[:200]
    except Exception as exc:
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["latency_ms"] = round((time.time() - t0) * 1000, 1)
    return record


async def upstream_probe(client: httpx.AsyncClient) -> dict:
    """Read upstream-reported prefill and prompt-cache counters."""
    payload = {
        "model": UPSTREAM_MODEL,
        "messages": [{"role": "user", "content": PROMPT[:PROMPT_CHARS]}],
        "max_tokens": MAX_TOKENS,
    }
    out: dict = {"available": False}
    try:
        resp = await client.post(f"{UPSTREAM}/v1/chat/completions", json=payload, timeout=TIMEOUT)
        body = resp.json()
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        timings = body.get("timings") or {}
        out = {
            "available": True,
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_prompt_tokens": details.get("cached_tokens"),
            "prefill_ms": timings.get("prompt_ms"),
            "predicted_tokens": timings.get("predicted_n"),
            "generation_tps": timings.get("generation_per_second"),
        }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}"
    return out


async def run_band(client: httpx.AsyncClient, concurrency: int) -> dict:
    swap_before = swap_counters()
    pressure_before = mem_pressure()

    t0 = time.time()
    records = await asyncio.gather(
        *(one_request(client, i) for i in range(concurrency))
    )
    wall = time.time() - t0

    swap_after = swap_counters()
    pressure_after = mem_pressure()

    ok = [r for r in records if r.get("ok")]
    tokens = sum(r.get("completion_tokens") or 0 for r in ok)
    latencies = [r["latency_ms"] for r in ok]

    swap_out_delta = None
    swap_in_delta = None
    if swap_before and swap_after:
        swap_out_delta = swap_after.get("swapouts", 0) - swap_before.get("swapouts", 0)
        swap_in_delta = swap_after.get("swapins", 0) - swap_before.get("swapins", 0)

    return {
        "concurrency": concurrency,
        "wall_seconds": round(wall, 2),
        "success_count": len(ok),
        "failure_count": concurrency - len(ok),
        "records": records,
        "aggregate_tokens_per_second": round(tokens / wall, 3) if wall > 0 else None,
        "per_request_tokens_per_second": (
            round(tokens / len(ok) / wall, 3) if ok and wall > 0 else None
        ),
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        "latency_max_ms": round(max(latencies), 1) if latencies else None,
        "swap_out_delta_pages": swap_out_delta,
        "swap_in_delta_pages": swap_in_delta,
        "memory_pressure_before": pressure_before,
        "memory_pressure_after": pressure_after,
    }


async def main() -> int:
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32)) as client:
        try:
            adm = await client.get(f"{BASE}/runtime/admission", timeout=20)
            admission = adm.json() if adm.status_code == 200 else {}
        except Exception:
            admission = {}

        print(f"experiment broker: {BASE}", file=sys.stderr)
        print(f"  max_active_requests : {admission.get('max_active_requests')}", file=sys.stderr)
        print(f"  queue_enabled       : {admission.get('queue_enabled')}", file=sys.stderr)
        print(f"  lane (via capacity) : {admission.get('capacity', {}).get('lane')}", file=sys.stderr)
        print(f"  lane_max_concurrent : {admission.get('capacity', {}).get('lane_max_concurrent')}", file=sys.stderr)
        print(f"  active_jobs at start: {admission.get('active_jobs')}", file=sys.stderr)
        print("", file=sys.stderr)

        # -- Warm-up phase (discarded) ----------------------------------
        # The model may have been evicted since it was last used.  A cold
        # request faults the weights back in from disk and produces a very
        # large swap delta that says nothing about concurrency.  Warm the
        # model first, and report the cold observation separately.
        swap_cold_before = swap_counters()
        cold = await one_request(client, -1)
        swap_cold_after = swap_counters()
        cold_swap_out = None
        cold_swap_in = None
        if swap_cold_before and swap_cold_after:
            cold_swap_out = (
                swap_cold_after.get("swapouts", 0) - swap_cold_before.get("swapouts", 0)
            )
            cold_swap_in = (
                swap_cold_after.get("swapins", 0) - swap_cold_before.get("swapins", 0)
            )
        print(
            f"warm-up (discarded): ok={cold.get('ok')} "
            f"latency={cold.get('latency_ms')}ms swap_out_delta={cold_swap_out}",
            file=sys.stderr,
        )

        results = []
        for concurrency in (1, 2):
            band = await run_band(client, concurrency)
            results.append(band)
            print(
                f"band x{concurrency}: ok={band['success_count']}/{band['concurrency']} "
                f"fail={band['failure_count']} "
                f"agg_tps={band['aggregate_tokens_per_second']} "
                f"lat_p50={band['latency_p50_ms']} "
                f"swap_out_delta={band['swap_out_delta_pages']}",
                file=sys.stderr,
            )

        upstream = await upstream_probe(client)

        try:
            rest = await client.get(f"{BASE}/runtime/admission", timeout=20)
            rest_state = rest.json()
        except Exception:
            rest_state = {}

    report = {
        "schema_version": "whooshd.lane_experiment.v1",
        "broker": BASE,
        "model": MODEL,
        "admission_at_start": {
            "max_active_requests": admission.get("max_active_requests"),
            "queue_enabled": admission.get("queue_enabled"),
            "lane_max_concurrent": admission.get("capacity", {}).get("lane_max_concurrent"),
        },
        "cold_start_observation": {
            "ok": cold.get("ok"),
            "latency_ms": cold.get("latency_ms"),
            "status": cold.get("status"),
            "error": cold.get("error"),
            "swap_out_delta_pages": cold_swap_out,
            "swap_in_delta_pages": cold_swap_in,
            "note": (
                "Warm-up request, discarded from the concurrency comparison. "
                "A large swap delta here is the model being faulted back in "
                "from disk, not concurrency thrash."
            ),
        },
        "bands": results,
        "upstream": upstream,
        "cleanup": {
            "active_jobs_at_rest": rest_state.get("active_jobs"),
            "queue_depth_at_rest": rest_state.get("queue_depth"),
        },
    }

    # Stability verdict, over the WARM bands only.
    failures = []
    for band in results:
        for record in band["records"]:
            if not record.get("ok"):
                failures.append(
                    f"band x{band['concurrency']} request failed: {record.get('error')}"
                )
        if band["swap_out_delta_pages"] and band["swap_out_delta_pages"] > 0:
            failures.append(
                f"band x{band['concurrency']} swapped OUT "
                f"{band['swap_out_delta_pages']} pages while warm (thrash signal)"
            )
    report["verdict"] = "PASS" if not failures else "FAIL"
    report["failures"] = failures

    print(json.dumps(report, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

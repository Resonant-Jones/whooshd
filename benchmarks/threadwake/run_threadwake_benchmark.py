#!/usr/bin/env python3
"""ThreadWake benchmark runner — in-process, no real model needed.

Uses ThreadWakeManager with FakeKVBackend (ephemeral mode) or
observe-only mode to measure cache hit rates, prefix reuse, and
memory estimates across synthetic prompt scenarios.

Usage:
    # Dry-run (observe-only, no KV reuse)
    python benchmarks/threadwake/run_threadwake_benchmark.py --dry-run

    # Ephemeral mode with FakeKVBackend
    python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --scenarios all

    # Specific format
    python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --format json
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

# Ensure the whooshd package is importable from the repo root.
sys.path.insert(0, ".")


from benchmarks.threadwake.report import (
    BenchmarkReport,
    FORMATTERS,
    ScenarioResult,
)
from benchmarks.threadwake.synthetic_prompts import (
    all_scenarios,
    get_scenario,
    list_scenarios,
)
from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ThreadWakeIndex
from whooshd.runtime.threadwake.tokenization import BackendTokenizerAdapterRegistry, FakeTokenizerAdapter
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
from whooshd.runtime.threadwake.types import ThreadWakeMode


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_request(messages, scenario, model="bench-model"):
    """Build a ChatCompletionRequest from messages and scenario config."""
    data = {
        "model": model,
        "messages": messages,
        "threadwake": {
            "enabled": True,
            "mode": scenario.mode,
            "scope": scenario.scope,
            "min_stable_prefix_tokens": 1,
        },
    }
    if scenario.thread_id:
        data["thread_id"] = scenario.thread_id
    return ChatCompletionRequest.model_validate(data)


def _run_observe_scenario(scenario, mgr) -> ScenarioResult:
    """Run a scenario in observe-only mode."""
    result = ScenarioResult(scenario_name=scenario.name, mode="observe")
    stable_tokens: list[int] = []
    dynamic_tokens: list[int] = []

    for batch in scenario.message_batches:
        req = _build_request(batch, scenario)
        obs = mgr.observe_request(req, backend="stub")
        result.request_count += 1
        if obs.eligible:
            result.eligible_count += 1
        if obs.cache_hit:
            result.hit_count += 1
        else:
            result.miss_count += 1
        if obs.stable_prefix_tokens:
            stable_tokens.append(obs.stable_prefix_tokens)
        if obs.dynamic_tokens:
            dynamic_tokens.append(obs.dynamic_tokens)
        result.estimated_prefill_tokens_reused += obs.estimated_prefill_reuse_tokens

    if stable_tokens:
        result.stable_prefix_tokens_avg = sum(stable_tokens) / len(stable_tokens)
    if dynamic_tokens:
        result.dynamic_tokens_avg = sum(dynamic_tokens) / len(dynamic_tokens)

    stats = mgr.get_health()
    result.memory_estimate_bytes = stats.get("estimated_memory_bytes", 0)
    return result


def _run_ephemeral_scenario(scenario, mgr, fake_kv) -> ScenarioResult:
    """Run a scenario in ephemeral mode with FakeKVBackend."""
    result = ScenarioResult(scenario_name=scenario.name, mode="ephemeral")
    stable_tokens: list[int] = []
    dynamic_tokens: list[int] = []

    def _gen(request, params):
        return [f"gen_{i}" for i in range(params.get("max_tokens", 4))]

    for i, batch in enumerate(scenario.message_batches):
        model = "bench-model"
        # For different-model scenario, alternate model IDs
        if scenario.name == "different-model" and i > 0:
            model = "bench-model-alt"

        req = _build_request(batch, scenario, model=model)
        ephem = mgr.execute_ephemeral(
            req, backend="fake", generate_fn=_gen,
        )
        result.request_count += 1
        if ephem.observation and ephem.observation.eligible:
            result.eligible_count += 1
        if ephem.cache_hit:
            result.hit_count += 1
        else:
            result.miss_count += 1
        if ephem.matched_tokens > 0:
            result.estimated_prefill_tokens_reused += ephem.matched_tokens
        if ephem.observation:
            if ephem.observation.stable_prefix_tokens:
                stable_tokens.append(ephem.observation.stable_prefix_tokens)
            if ephem.observation.dynamic_tokens:
                dynamic_tokens.append(ephem.observation.dynamic_tokens)

    if stable_tokens:
        result.stable_prefix_tokens_avg = sum(stable_tokens) / len(stable_tokens)
    if dynamic_tokens:
        result.dynamic_tokens_avg = sum(dynamic_tokens) / len(dynamic_tokens)

    stats = mgr.get_health()
    result.memory_estimate_bytes = stats.get("estimated_memory_bytes", 0)
    return result


# ── Main ───────────────────────────────────────────────────────────────────


def run_benchmarks(
    mode: str = "observe",
    scenario_names: list[str] | None = None,
) -> BenchmarkReport:
    """Run all or selected scenarios and return a report."""
    if scenario_names is None:
        scenarios = all_scenarios()
    else:
        scenarios = [get_scenario(name) for name in scenario_names]

    # Build manager
    if mode == "ephemeral":
        fake_kv = FakeKVBackend()
        fake_tok = FakeTokenizerAdapter()
        registry = BackendKVAdapterRegistry()
        tok_registry = BackendTokenizerAdapterRegistry()
        registry.register("fake", fake_kv)
        tok_registry.register("fake", fake_tok)
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            tokenizer_registry=tok_registry,
            index=ThreadWakeIndex(max_entries=50),
        )
    else:
        fake_kv = None
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            index=ThreadWakeIndex(max_entries=50),
        )

    results: list[ScenarioResult] = []
    for scenario in scenarios:
        # Override scenario mode to match benchmark mode
        scenario.mode = mode

        if mode == "observe" or mode == "dry-run":
            result = _run_observe_scenario(scenario, mgr)
        elif mode == "ephemeral":
            result = _run_ephemeral_scenario(scenario, mgr, fake_kv)
        else:
            result = ScenarioResult(
                scenario_name=scenario.name, mode=mode,
                errors=[f"Unsupported mode: {mode}"],
            )

        results.append(result)

    # Aggregate
    total_requests = sum(r.request_count for r in results)
    total_hits = sum(r.hit_count for r in results)
    total_misses = sum(r.miss_count for r in results)
    total_prefill_reused = sum(r.estimated_prefill_tokens_reused for r in results)

    return BenchmarkReport(
        mode=mode,
        scenarios=results,
        total_requests=total_requests,
        total_hits=total_hits,
        total_misses=total_misses,
        total_prefill_reused=total_prefill_reused,
    )


def main():
    parser = argparse.ArgumentParser(
        description="ThreadWake Benchmark — measure cache effectiveness in-process",
    )
    parser.add_argument(
        "--mode", default="observe",
        choices=["observe", "dry-run", "ephemeral"],
        help="Benchmark mode: observe (no KV reuse), ephemeral (FakeKVBackend reuse)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Shortcut for --mode dry-run",
    )
    parser.add_argument(
        "--scenarios", default="all",
        help="Comma-separated scenario names, or 'all'. Available: " + ", ".join(list_scenarios()),
    )
    parser.add_argument(
        "--format", default="console",
        choices=["console", "json", "markdown"],
        help="Output format",
    )

    args = parser.parse_args()

    # Normalize mode
    if args.dry_run:
        args.mode = "dry-run"
    mode = "observe" if args.mode == "dry-run" else args.mode

    # Parse scenarios
    if args.scenarios == "all":
        scenario_names = None
    else:
        scenario_names = [s.strip() for s in args.scenarios.split(",")]

    report = run_benchmarks(mode=mode, scenario_names=scenario_names)

    formatter = FORMATTERS.get(args.format, FORMATTERS["console"])
    output = formatter(report)
    print(output)


if __name__ == "__main__":
    main()

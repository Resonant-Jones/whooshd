# ThreadWake Benchmark Harness

Measures ThreadWake cache effectiveness across observe, ephemeral,
and session modes using synthetic prompts and an in-process
FakeKVBackend.  No real model inference required — measures
ThreadWake overhead and cache hit/miss behaviour.

## Quick Start

```bash
# Dry-run (observe-only, no KV reuse)
python benchmarks/threadwake/run_threadwake_benchmark.py --dry-run

# Ephemeral mode with FakeKVBackend
python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --scenarios all

# Specific scenario
python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --scenarios large-prefix

# Output as JSON
python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --format json

# Output as Markdown
python benchmarks/threadwake/run_threadwake_benchmark.py --mode ephemeral --format markdown
```

## Scenarios

| # | Name | Description |
|---|------|-------------|
| 1 | `small-prompt` | Short prompt — should be ineligible or low value |
| 2 | `large-prefix` | Large stable system prompt + small user turn |
| 3 | `persona-prefix` | Stable persona/tool/project prefix + changing user messages |
| 4 | `session-continuation` | Session continuation across 5 turns |
| 5 | `changed-prefix` | Changed prefix causing cache miss |
| 6 | `different-model` | Different model causing cache miss |

## Metrics

- `request_count` — total requests run
- `eligible_count` — requests eligible for KV reuse
- `hit_count` — cache hits (prefix matched)
- `miss_count` — cache misses
- `stable_prefix_tokens_avg` — average stable prefix token count
- `dynamic_tokens_avg` — average dynamic tail token count
- `estimated_prefill_tokens_reused` — total prefill tokens skipped via cache hits
- `memory_estimate` — estimated memory used by cached entries

## Requirements

- Python 3.13+
- No cloud services
- No private user data
- All prompts are synthetic/generated

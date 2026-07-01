# Live MLX ThreadWake Smoke

Manual smoke test that proves the experimental MLX prompt-cache path
works against a real local MLX model.

## Purpose

Prove that ThreadWake's experimental MLX KV reuse path:

- Starts with MLX adapter + ephemeral mode enabled
- Reports `experimental` MLX KV capability
- Completes real chat requests against a loaded model
- Observes cache activity (entry creation, hit/miss)
- Does not leak prompts, token IDs, or cache internals through runtime surfaces

## Prerequisites

- Apple Silicon (M-series)
- macOS 14+
- `mlx-lm` installed (`pip install mlx-lm`)
- A local MLX model available (e.g. `mlx-community/Llama-3.2-3B-Instruct-4bit`)
- Whoosh'd installed with `[mlx]` extra

## Running

Start the server:

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_THREADWAKE_ENABLED=true \
WHOOSHD_THREADWAKE_MODE=ephemeral \
WHOOSHD_THREADWAKE_MLX_TOKENIZER_ENABLED=true \
WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true \
WHOOSHD_MLX_MODEL="mlx-community/Llama-3.2-3B-Instruct-4bit" \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Wait for the model to load (check `/ready` returns `200`), then run:

```bash
sh scripts/smoke_threadwake_mlx_live.sh
```

Optional overrides:

```bash
WHOOSHD_BASE_URL=http://127.0.0.1:8000 \
WHOOSHD_MLX_SMOKE_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
sh scripts/smoke_threadwake_mlx_live.sh
```

## Expected Output

```text
Whoosh'd Live MLX ThreadWake Smoke
  base:  http://127.0.0.1:8000
  model: mlx-community/Llama-3.2-3B-Instruct-4bit

  PASS  /health reachable
  PASS  /ready reachable
  PASS  /v1/models contains selected model
  PASS  ThreadWake enabled
  PASS  ThreadWake mode is ephemeral
  PASS  MLX KV capability is experimental
  PASS  first request returned valid chat completion
  PASS  second request returned valid chat completion
  PASS  first request produced non-empty content
  PASS  second request produced non-empty content
  PASS  ThreadWake entry count increased after requests
  PASS  ThreadWake cache activity observed
  PASS  no request failure observed
  PASS  health surfaces do not leak prompt text
  PASS  health surfaces do not leak opaque cache internals
  PASS  health surfaces do not leak generated text

---
Passed: 16  Failed: 0
```

## Interpretation

- **All PASS**: The experimental MLX prompt-cache path is working against
  a real loaded model. Cache entries are being created and hit/miss
  activity is observed.

- **MLX KV capability is unsupported**: The experimental flag is not set.
  Check `WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true`.

- **ThreadWake cache activity not observed**: The cache hit/miss counters
  may not increment if the adapter falls back. This can happen if the
  tokenizer or KV adapter fails to register. Check the server logs.

## Known Limitations

This smoke is **not a benchmark**. It proves behavioral correctness, not
performance. Specifically:

- Experimental mode only — not production-ready
- Clone is not supported
- Disk persistence is not supported
- Concurrency safety is not proven
- Scheduler / batching are not included
- No performance acceleration claims are made

See `docs/threadwake-mlx-kv-feasibility.md` for the full feasibility report.

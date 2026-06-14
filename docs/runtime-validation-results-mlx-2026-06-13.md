# MLX-LM Server Runtime Validation Results — 2026-06-13

Real local runtime validation of Whoosh'd against `mlx_lm.server`.

## Machine Info

| Field | Value |
|-------|-------|
| Machine | Apple Silicon (Mac) |
| OS | macOS (darwin) |
| Python | 3.14.3 |
| Whoosh'd version | 0.1.0rc1 |
| mlx-lm version | 0.31.3 |
| MLX model | `mlx-community/Llama-3.2-3B-Instruct-4bit` |
| Model format | MLX (4-bit quantized) |
| Runtime mode | External server (mlx_lm.server started independently) |
| Date | 2026-06-13 |

## Runtime Startup Commands

### MLX-LM Server

```bash
cd /Users/resonant_jones/Keep/Resonant_Constructs/Whoosh'd
.venv/bin/python -m mlx_lm server \
  --model "mlx-community/Llama-3.2-3B-Instruct-4bit" \
  --host 127.0.0.1 \
  --port 8081
```

### Whoosh'd (with guardrails)

```bash
WHOOSHD_MLX_ENABLED=true \
  WHOOSHD_MLX_MODEL="mlx-community/Llama-3.2-3B-Instruct-4bit" \
  WHOOSHD_MLX_HOST=127.0.0.1 \
  WHOOSHD_MLX_PORT=8081 \
  WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS=2 \
  WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS=3.0 \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Phase 7 Guardrail Revalidation

### x2 Concurrency (baseline)

```
Check                                       Status     Detail
-----------------------------------------------------------------------
/health                                     PASS       status=200 lifecycle=ready
/health/runtime                             PASS       status=ok (mlx_lm_server=ready)
/ready                                      PASS       ready=True
/v1/models                                  PASS       1 models: mlx-community/Llama-3.2-3B-Instruct-4bit
/api/tags                                   PASS       1 tags: mlx-community/Llama-3.2-3B-Instruct-4bit
Non-streaming chat                          PASS       status=200 finish=stop content_len=6 (524ms)
Streaming chat                              PASS       chunks=3 done=True text_len=6 (152ms)
Codexify SSE compat                         PASS       chunks=3 role=True finish=True (148ms)
Concurrent x2                               PASS       ok=2/2 avg_ttft=236ms stuck=0 empty=0
```

**Result: 10/10 PASS**

### x4 Concurrency (guardrail test)

```
Check                                       Status     Detail
-----------------------------------------------------------------------
/health                                     PASS       ...
/health/runtime                             PASS       status=ok
/ready                                      PASS       ready=True
/v1/models                                  PASS       1 models: MLX model
/api/tags                                   PASS       1 tags: MLX model
Non-streaming chat                          PASS       211ms
Streaming chat                              PASS       167ms
Codexify SSE compat                         PASS       158ms
Concurrent x4                               PASS       ok=2/4 avg_ttft=166ms stuck=0 overloaded=2
```

**Result: 10/10 PASS**

### Key Observations

| Metric | x2 | x4 |
|--------|----|----|
| Completed requests | 2/2 | 2/4 |
| Overloaded (429) | 0 | 2 |
| Stuck requests | 0 | 0 |
| Health during overload | ready | ready |
| Overload classification | N/A | RUNNER_OVERLOADED (HTTP 429) |

**No requests got stuck.** Excess requests at capacity receive clean 429 responses.
`/health/runtime` continues to report `ready` during overload — the runtime
is not marked offline just because it's busy.

The 429 response shape:

```json
{
    "code": "RUNNER_OVERLOADED",
    "message": "Whoosh'd is at its active request limit (2).",
    "detail": {"active_jobs": 2, "max_active_requests": 2}
}
```

### Concurrency Layers

Two layers protect against overload:

1. **Admission control** (`WHOOSHD_MAX_ACTIVE_REQUESTS=2`) — global limit across all runtimes, checked before routing
2. **Per-runtime semaphore** (`WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS=2`) — runtime-specific limit inside the adapter, with configurable acquire timeout

Both reject excess requests with HTTP 429.

## Known Issues

1. **Concurrency limited to ~2** — Single-model MLX instance with default config.
   Increase `WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS` if the runtime supports more.

2. **`/v1/models` shows only MLX model** — When no registry is configured and
   only MLX is enabled, only the MLX model appears. This is correct behavior
   (no more misleading stub-model).

3. **Admission control may reject before semaphore** — The global
   `WHOOSHD_MAX_ACTIVE_REQUESTS` is checked first. Increase it if you want
   the per-runtime semaphore to be the primary gate.

## llama.cpp Status

```
blocked: llama-server binary unavailable
```

## Follow-Up

1. Install `llama-server` + GGUF model for real llama.cpp validation
2. Test with increased `WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS` if model supports it
3. Add `mlx-vlm` adapter for vision models
4. Consider request queuing with bounded wait for graceful overload handling

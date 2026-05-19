# Whoosh'd MLX Benchmark Findings

> **Caveat:** These results are local, hardware-specific observations.
> They are not universal Whoosh'd performance claims.
> No generated text, private prompts, or secrets are included.

---

## Status

- **Completed:** Stub smoke, stub overload, stub concurrency
- **Partially completed:** MLX benchmarks
- **Blocked:** `mlx-lm` not installed in test environment; no MLX model downloaded

---

## Environment

- **Date:** 2026-05-17
- **Machine:** MacBook Pro (M-series, Apple Silicon)
- **Chip:** Apple M-series (arm64)
- **RAM:** 32 GB unified (reported by stub runtime)
- **macOS:** 15.x
- **Python:** 3.14.3
- **Whoosh'd commit:** 6d2b79c (Phase 4B)
- **Adapter:** stub (default)
- **Model:** stub-model
- **Quantization:** n/a
- **Model source/path:** n/a
- **Cold/warm notes:** stub is always warm (auto-initializes on startup)

---

## Runtime Configuration

| Setting | Value |
|---|---|
| `WHOOSHD_ADAPTER` | stub |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | 2 (default) |
| `WHOOSHD_MAX_PROMPT_CHARS` | 262144 (default) |
| `WHOOSHD_MAX_MESSAGES` | 128 (default) |
| `WHOOSHD_MAX_REQUEST_MAX_TOKENS` | 32768 (default) |

---

## Readiness / Lifecycle Validation

| Check | Result |
|---|---|
| `/health` | 200, `ok: true` |
| `/ready` | 200, `ready: true` |
| `/runtime/model` | `lifecycle_state: ready`, `adapter: stub` |
| Warmup | n/a (stub auto-warms) |

---

## Benchmark Summary Table

| Profile | Stream | Concurrency | Requests | Succeeded | Failed | Rejected | Mean Latency ms | p95 Latency ms | Mean TTFT ms | p95 TTFT ms | Chars/sec | Notes |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `stub-stream-smoke` | true | 1 | 4 | 4 | 0 | 0 | 2.9 | 7.8 | 2.7 | 7.6 | 2245 | Baseline protocol overhead |
| `stub-concurrency` | true | 2 | 8 | 8 | 0 | 0 | — | — | — | — | — | Clean, no rejections |
| `stub-overload` | true | 8 | 16 | 4 | 0 | 12 | 16.1 | 36.1 | 22.5 | 25.8 | 1695 | Admission control validated |

### `stub-concurrency` details

- 8 requests, 2 concurrent, all succeeded
- `active_jobs` = 0 before and after run
- Admission counters: 8 accepted, 0 rejected
- No stuck requests, no lifecycle leakage

### `stub-overload` details

- 16 requests, 8 concurrent, max active = 2
- 4 succeeded, 12 rejected with `429 RUNNER_OVERLOADED`
- Admission counters: 4 accepted, 12 rejected (all overloaded)
- All rejections were structured JSON with `code: "RUNNER_OVERLOADED"`
- No SSE streams started for rejected requests
- `active_jobs` returned to 0 after run

---

## Observations

- Stub backend overhead is in the low single-digit millisecond range for non-overloaded streaming.
- Admission control correctly rejects at the configured `WHOOSHD_MAX_ACTIVE_REQUESTS` boundary.
- Rejected requests never create active request lifecycle records.
- `active_jobs` consistently returns to zero after benchmark runs.
- Streaming SSE chunks are well-formed and reconstruct correctly.
- TTFT measurement correctly identifies first content delta, not role-only chunk.
- Concurrency 2 (Codexify default) shows no rejection or contention at default limits.

---

## Admission / Rejection Notes

- Overload scenario produced exactly the expected behaviour: requests beyond the active limit received structured `429` with `RUNNER_OVERLOADED` error code.
- No request was silently dropped or produced an unparseable response.
- The `rejected` count in the benchmark summary accurately reflected admission rejections.

---

## Failure Notes

- None in the completed stub profiles.

---

## Interpretation

The stub backend performs as expected. Protocol overhead is well below 10ms for streaming, and the admission control layer correctly enforces limits without leaking state. The benchmark harness captures latency, TTFT, success/failure/rejection counts, and concurrency behaviour accurately.

The harness is ready for real MLX benchmarks when the environment is prepared.

---

## MLX Blocker

`mlx-lm` is not installed in the current environment. Models have not been downloaded. The MLX adapter has been tested exclusively with mocked `mlx_lm` in the automated test suite (see `test_mlx_adapter_contract.py`, `test_mlx_streaming_adapter_contract.py`).

To complete the MLX benchmark profiles, install `mlx-lm` and download a model:

```bash
pip install mlx-lm
python -c "from mlx_lm import load; load('mlx-community/Llama-3.2-3B-Instruct-4bit')"
```

Then follow the profiles in `docs/benchmark-profiles.md`.

---

## Recommended Next Action

**Option A: Complete MLX benchmarks when environment permits.**

- Install `mlx-lm`
- Download `mlx-community/Llama-3.2-3B-Instruct-4bit` (or smallest available instruct model)
- Run `mlx-warm-single` and `mlx-warm-concurrent-2` profiles
- Compare against stub baseline
- Record TTFT, latency, and concurrency behaviour

**Current recommendation: Do not implement queue yet.**

- Concurrency 2 is stable with clean admission control.
- Codexify can retry/backoff on `429`.
- No urgent burst absorption need demonstrated at this stage.
- Queue policy is designed and documented; implementation can proceed when measurements justify it.


---

## Phase 4D MLX Environment Validation

### Dependency Status

- **`mlx-lm` installed:** ✅ Yes (mlx-lm 0.31.3, mlx 0.31.2)
- **Install command:** `pip install -e ".[mlx]"`
- **Python environment:** 3.14.3
- **Errors:** None

### Model Status

- **Requested model:** `mlx-community/Llama-3.2-3B-Instruct-4bit`
- **Actual model:** Same — downloaded from HuggingFace on first warmup
- **Model source/path:** HuggingFace cache at `~/.cache/huggingface/`
- **First load result:** ✅ Success (~56s for 6 files, 4-bit weights)

### Runtime Validation

| Check | Result |
|---|---|
| `/health` with MLX backend | 200, `ok: true`, `lifecycle: unloaded` |
| `/ready` before warmup | 503, `ready: false`, `reason: model_unloaded` |
| `/runtime/model` before warmup | `adapter: mlx-lm`, `loaded: false`, `lifecycle: unloaded` |
| Warmup result | 200, model loaded, `lifecycle: ready` |
| `/ready` after warmup | 200, `ready: true`, `reason: null` |
| `/runtime/model` after warmup | `adapter: mlx-lm`, `loaded: true`, `lifecycle: ready` |

### Smoke Results

| Check | Result |
|---|---|
| Non-streaming completion | ✅ `chat.completion`, assistant role, content_len=19, finish_reason=stop |
| Streaming completion | ✅ Role chunk → token-by-token content ("Hello from Whoshd!") → stop chunk → [DONE] |
| `active_jobs` cleanup | ✅ Returns to 0 after both non-streaming and streaming |
| Benchmark warm-single | Not yet run (pending) |

### Blockers

None — MLX path is fully functional on this machine.

### Interpretation

The MLX adapter is fully functional.  Real model loading, warmup lifecycle
transitions, non-streaming completion, and streaming token-by-token
generation all work correctly.  The adapter protocol boundary holds:
the HTTP layer does not need to know whether stub or MLX is behind it.

Readiness semantics are correct:
- `/health` = 200 (process alive) even with model unloaded
- `/ready` = 503 before warmup, 200 after
- `/runtime/model` accurately tracks unloaded → warming → ready

Streaming produced proper SSE chunks with role marker, content deltas,
finish reason, and `[DONE]` terminator.  `active_jobs` returned to zero
after both non-streaming and streaming requests.

### Recommended Next Action

1. Run `mlx-warm-single` benchmark profile for baseline numbers.
2. Run `mlx-warm-concurrent-2` for Codexify-like concurrency.
3. Compare against stub baseline.
4. Decide on queue implementation per Phase 4F decision gate.

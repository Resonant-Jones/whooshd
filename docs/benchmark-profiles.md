# Benchmark Profiles

Canonical benchmark scenarios for Whoosh'd.

These are operator recipes, not magic.  Each profile describes what to run,
how to interpret results, and what not to conclude.

---

## Profile Definitions

### `stub-smoke`

Validate the benchmark CLI and protocol path.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 1 \
  --requests 4
```

**Expected:** 4 succeeded, 0 failed, 0 rejected.  

**Does not** represent model performance.

---

### `stub-stream-smoke`

Validate streaming/TTFT path.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 1 \
  --requests 4 \
  --stream
```

**Expected:** 4 succeeded, TTFT values present, chunks > 0.  

**Does not** represent model performance.

---

### `stub-concurrency`

Validate concurrency accounting and admission control works.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 2 \
  --requests 8 \
  --stream
```

**Expected:** 8 succeeded, 0 rejected at default limits.  

Verifies `active_jobs` returns to zero after run.

---

### `stub-overload`

Validate admission/rejection behaviour under overload.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 8 \
  --requests 16 \
  --stream
```

**Expected:** With `WHOOSHD_MAX_ACTIVE_REQUESTS=2`, some requests return `429 RUNNER_OVERLOADED`.  
The `rejected` count should be > 0.

---

### `mlx-cold-start`

Measure first-load / cold-start path.  Manual only.

```bash
# Ensure model is not loaded
curl -X POST http://localhost:8000/runtime/model/unload

python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 1 \
  --requests 1
```

**Expected:** Single request succeeds, but latency includes model load time.  
**Note:** First MLX run may download the model if not cached.

---

### `mlx-warm-single`

Measure warmed streaming baseline.  Manual only.

```bash
# Warm up first
curl -X POST http://localhost:8000/runtime/model/warmup

python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 1 \
  --requests 4 \
  --stream \
  --max-tokens 128
```

**Expected:** All succeed, TTFT relatively low, no rejections.

---

### `mlx-warm-concurrent-2`

Codexify-like concurrency.  Manual only.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 2 \
  --requests 8 \
  --stream \
  --max-tokens 128
```

**Expected:** All succeed at default `WHOOSHD_MAX_ACTIVE_REQUESTS=2`.

---

### `mlx-warm-concurrent-4`

Explore headroom above Codexify default.  Manual only.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 4 \
  --requests 12 \
  --stream \
  --max-tokens 128
```

**Expected:** Depends on hardware.  May show increased p95 latency or memory pressure.

---

### `mlx-overload`

Verify admission rejection under load.  Manual only.

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 8 \
  --requests 16 \
  --stream \
  --max-tokens 128
```

**Expected:** Some requests rejected with `429 RUNNER_OVERLOADED`.  Rejection count > 0.

---

## Cold vs Warm

- **Cold run:** model not loaded before benchmark.  Includes model load / download cost.
- **Warm run:** model already loaded via `POST /runtime/model/warmup`.

Always note cold/warm in reports.

---

## Interpretation Guide

| Signal | Possible meaning | Do next |
|---|---|---|
| High TTFT on cold run only | Model load/warmup cost | Use warmup, measure warm run |
| High TTFT on warm run | Prompt formatting/prefill/model latency | Inspect prompt/context size |
| Many `429` rejections | Active limit too low or caller too bursty | Consider queue policy later |
| `active_jobs` stuck after benchmark | Lifecycle/cancel bug | Fix before throughput work |
| p95 much larger than p50 | Contention or backend variability | Measure with lower concurrency |
| Stub slow | HTTP/runtime overhead | Profile app layer |
| MLX non-streaming ok but streaming poor | Stream bridge/generator issue | Inspect stream adapter |

---

## Important Warnings

- **Stub results are not model performance.** They measure protocol/runtime overhead only.
- **Character throughput is not token throughput.**
- **Results depend on hardware, RAM pressure, quantization, context length, and max_tokens.**
- **Do not include private prompts, generated text, or secrets in benchmark reports.**
- **Automated tests must never download models.**

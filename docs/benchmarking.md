# Whoosh'd Benchmarks

How to measure Whoosh'd throughput.

---

## Purpose

The benchmark harness measures Whoosh'd from the outside — over HTTP, the same
way Codexify calls it.  It is instrumentation, not optimization.

It answers:

- How fast is the server?
- What is TTFT for streaming?
- How many requests succeed vs fail vs get rejected?
- How does concurrency affect behaviour?

It does **not** tune performance, implement queueing, or make claims about
model quality.

---

## Quick Start

```bash
# Non-streaming stub benchmark
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 1 \
  --requests 10

# Streaming stub benchmark
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model stub-model \
  --concurrency 2 \
  --requests 8 \
  --stream true

# JSON output
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --concurrency 2 \
  --requests 4 \
  --stream true \
  --json
```

---

## MLX Benchmarks

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --reload

# Warm up first
curl -X POST http://localhost:8000/runtime/model/warmup

# Non-streaming
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 2 \
  --requests 8 \
  --max-tokens 128

# Streaming
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 2 \
  --requests 8 \
  --stream true \
  --max-tokens 128
```

---

## What the Harness Measures

### Non-Streaming

- Request start to response received (total latency)
- HTTP status code
- Success / failure / rejection classification
- Visible response characters
- Structured error codes from rejection responses

### Streaming

All of the above, plus:

- TTFT (time to first **content** delta — not role-only chunk)
- Number of data chunks received
- Visible characters reconstructed from SSE

---

## TTFT Definition

```text
TTFT = time from request start to first chunk where delta.content is not null.
```

The first chunk (role-only, `delta: {role: "assistant"}`) does **not** count as
TTFT.  Codexify cares about when visible text appears.

---

## Concurrency

The harness uses an `asyncio.Semaphore` to bound concurrent in-flight requests.
It does **not** coordinate with Whoosh'd internals.

Example: 16 total requests with concurrency 4 runs at most 4 at a time.

---

## Admission Rejection Interpretation

If `WHOOSHD_MAX_ACTIVE_REQUESTS=2` and the harness sends requests with
concurrency 8, some requests will be rejected with `429 RUNNER_OVERLOADED`.
The benchmark counts these as `rejected`, not as failures.

Use this to validate admission control behaviour.

---

## Known Limitations

- Stub backend numbers are protocol/runtime measurements, not model performance.
- MLX results depend heavily on model, quantization, hardware, memory pressure,
  warmup state, and max_tokens.
- Character throughput is not token throughput.
- The harness does not implement queueing or scheduling.
- TTFT is measured at the client, not at the server.
- Network latency on localhost is negligible but not zero.

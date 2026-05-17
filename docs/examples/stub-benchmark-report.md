# Stub Benchmark Report — Sample

> **This report validates benchmark harness behavior only.**
> Stub backend results are not model throughput results.

---

## Run Metadata

- **Date:** 2026-05-17
- **Operator:** automated
- **Machine:** MacBook Pro (example)
- **Chip:** M4 Pro (example)
- **RAM:** 32 GB (example)
- **macOS version:** 15.x (example)
- **Python version:** 3.12 (example)
- **Whoosh'd commit:** 9c149c2
- **Adapter:** stub
- **Model:** stub-model
- **Quantization:** n/a
- **Model source/path:** n/a
- **Cold or warm:** warm (stub is always loaded)
- **Warmup method:** auto on startup
- **Max active requests:** 2 (default)
- **Queue enabled:** false
- **Notes:** Smoke test.  No MLX, no model download.

---

## Scenario

- **Profile:** stub-stream-smoke
- **Stream:** true
- **Concurrency:** 2
- **Requests:** 4
- **Max tokens:** 64
- **Prompt class:** short_hello
- **Prompt chars:** < 50
- **Expected behavior:** All succeed, TTFT measurable, no rejections.

---

## Results

- **Succeeded:** 4
- **Failed:** 0
- **Rejected:** 0
- **Mean latency ms:** ~5.5
- **p50 latency ms:** ~4.7
- **p95 latency ms:** ~10.5
- **Mean TTFT ms:** ~5.1
- **p50 TTFT ms:** ~4.4
- **p95 TTFT ms:** ~10.2
- **Visible chars:** 124
- **Chars/sec:** ~2591

Results are from an automated test run and represent protocol/runtime
overhead only.  Latency numbers are in the single-digit millisecond range
for the stub backend, confirming the harness and HTTP layer are functional.

---

## Observations

- All requests completed successfully.
- TTFT values are small and consistent with stub response behaviour.
- No admission rejections at concurrency 2 with default limits.

---

## Failure / Rejection Notes

- None.

---

## Interpretation

The benchmark harness is functional.  Stub backend results provide a
noise floor for protocol overhead.  Real MLX benchmarks should be
compared against this baseline.

---

## Next Action

- Run MLX warm benchmark profiles.
- Compare MLX results against this stub baseline.
- Measure admission rejection under overload.

# Runtime Calibration Report

**Schema:** `whooshd.calibration.v1`  
**Captured:** 2026-11-03T02:00:49.801622+00:00  
**Host:** macOS-27.2-arm64-arm-64bit  
**Metal in harness process:** False (RuntimeError: [metal::load_device] No Metal device available. This typically occurs in headless, sandboxed, or virtualized macOS sessions where the GPU is not accessible.)

**Target:** `http://127.0.0.1:8000` model `local-chat` prompt 2048 chars, max_tokens 128

## Concurrency bands

| Band | OK | 429 | Fail | TTFT p50 | TTFT p95 | Lat p50 | Prefill p50 | Agg tok/s | Queue p50 | Swap Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| x1 | 1/2 | 1 | 0 | 4781.1 | 4781.1 | 14389.4 | 276.0 | 6.9 | — | 0 |
| x2 | 1/2 | 1 | 0 | 497.1 | 497.1 | 10375.1 | 274.3 | 7.3 | — | 0 |

## Cache effectiveness

- Upstream cache counters available: **True**
- Cached prompt tokens: **698**
- Prompt tokens: **730**
- Cache hit ratio: **0.956164**
- Upstream-reported prefill (ms): **271.62550000139163**

## Leak check (at rest)

- `active_jobs` at rest: **0**
- `queue_depth` at rest: **0**
- Leaked work: **False**

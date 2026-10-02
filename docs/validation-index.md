# Validation Index

## What Validation Means

A validation result is **scoped evidence**. It does not imply production
readiness, latency improvement, or throughput improvement unless those claims
are explicitly validated and documented.

## Status Vocabulary

| Status | Meaning |
|---|---|
| **PASSED** | Validation criteria met within the documented scope |
| **FAILED** | Validation criteria not met; fix required |
| **INCONCLUSIVE** | Test-environment issue or incomplete precondition; no judgment |
| **BLOCKED** | Could not be attempted due to a recorded external blocker |
| **PENDING** | Not yet performed; prerequisites may or may not be present |

**BLOCKED and PENDING are not PASSED.** Hardware-blocked or unperformed
evidence is never reported as a successful result, and an earlier result that
has since been resolved is annotated rather than silently erased.

## Current Results

| Packet | Status | Date | Scope |
|---|---|---|---|
| Throughput control plane — real MLX plumbing (Gemma 4 E2B) | PASSED | 2026-08-19, requalified 2026-09-11 | Control-plane mechanics, admission, queue, scheduler, capacity gating, cancellation, isolation. Not a 12B throughput claim |
| Throughput control plane — full-suite baseline | PASSED | 2026-10-02 | 2360 passed, 3 skipped. The campaign-era "17 failed" figure is resolved; see the report's dated amendment |
| Capacity control plane automation | PASSED | 2026-10-02 | 37 tests across profile schema/round-trip, identity matching, operator ceilings, queue/scheduler authority, cancellation, SSE timing, batch interaction |
| Loopback deployment containment proof | PASSED | 2026-07-18 | [packet](validation/2026-07-18-whooshd-loopback-deployment-containment-proof.md) |
| CWC-009.1 live cross-process request correlation | PASSED | 2026-07-22 | [packet](validation/2026-07-22-cwc009-1-live-cross-process-request-correlation.md). Not a supported-profile or release-readiness claim |
| ThreadWake MLX live smoke | PASSED | 2026-07-01 | [packet](validation/threadwake-mlx-live-smoke-result.md). MLX KV capability reported **experimental**, never production |
| Guarded adapter batching smoke harness | PASSED | 2026-07-02 | Explicit gated conditions |
| Guarded adapter batching HTTP grouping | PASSED | 2026-07-02 | Does not demonstrate live-path batch-group formation |
| Guarded adapter batching initial | INCONCLUSIVE | 2026-07-02 | Superseded by the two results above |
| Cave Thunder decision | RECORDED | 2026-07-02 | Token-step shared decode remains MLX-blocked |
| Gemma 4 12B production capacity calibration | **PENDING** | — | Host (`Mac16,10` M4 / 32 GB) and a complete ~10 GB local checkpoint are **present**; the benchmark run has **not** been performed. Not a GA blocker |
| Gemma 4 E4B cross-model portability smoke | **BLOCKED** | 2026-08-19 | `mlx_vlm` rejected 126 checkpoint parameters — `MLX_RUNTIME` / `MODEL` incompatibility. Explicit follow-up, not a passing result |

## How to Run Validations

Smoke harness:

```bash
python scripts/smoke_guarded_mlx_adapter_batching_runtime.py
```

HTTP grouping (requires a running server):

```bash
python -m pytest tests/test_guarded_adapter_batch_http_grouping_validation.py
```

Stub smoke checks:

```bash
sh scripts/smoke_stub.sh
sh scripts/smoke_openai_compat.sh
```

Capacity calibration (requires a Metal-capable host with the MLX runtime
installed):

```bash
python -m whooshd.bench.capacity_bench --help
```

## What Validation Does Not Imply

- Production readiness
- Latency or throughput improvement
- Token-step shared decode scheduling support
- Default enablement
- Portability of a capacity profile across models, runtimes, or hosts
- Production ThreadWake KV reuse

See [Throughput Control Plane Report](reports/throughput-control-plane.md) for
the full recorded campaign and its claim boundaries.

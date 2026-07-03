# Guarded MLX Adapter-Batching Operator Guide

Experimental. Explicitly gated. Disabled by default. Smoke-harness and HTTP grouping validated.
Not production-ready.

## What It Is

Guarded MLX adapter batching is an explicitly gated experimental path for
compatible MLX text-only non-streaming requests. It groups compatible
requests through the guarded adapter-batch runner and preserves virtual
slot lifecycle, tombstones, controlled errors, metadata-only reporting,
and OpenAI-compatible response-shape checks.

## What It Is Not

Guarded MLX adapter batching is **not true token-step continuous batching**.
It does not implement shared decode-loop scheduling. It is not enabled by
default. It is not production-ready. It does not claim latency or throughput
improvement.

HTTP queue/admission grouping has been validated under explicit guarded
adapter-batch test conditions, but that validation does not make the
feature production-ready and does not imply performance improvement.

## Validation Status

- Initial runtime validation: **inconclusive** (enabled live-path grouping not configured)
- Smoke-harness validation: **passed** with `group_formed=true`
- HTTP queue/admission grouping validation: **passed** under explicit test conditions

Two compatible requests entered the full HTTP queue/admission path, queued
behind a blocker, completed successfully, preserved OpenAI-compatible
response shape, leaked no internal metadata, drained the queue, and
returned `active_jobs` to 0.

## Enablement Flags

```bash
export WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=true
```

**Both flags required.** Default: `false`.

## Disablement and Rollback

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

## Claim Boundary

| Claim | Allowed? | Notes |
|---|---|---|
| Guarded MLX adapter batching exists | Yes | Experimental, gated |
| Smoke harness passes | Yes | Smoke-harness scope |
| HTTP queue/admission grouping validated | Yes | Explicit test conditions |
| Metadata/privacy checks pass | Yes | No internal metadata leak |
| Production-ready | No | `production_ready=false` |
| Latency improvement | No | Not benchmarked |
| Throughput improvement | No | Not benchmarked |
| True token-step continuous batching | No | Future work |
| Shared decode-loop scheduling | No | Future work |
| VLM batching | No | Unsupported |
| Streaming batching | No | Unsupported |

## Related Docs

- `docs/guarded-adapter-batch-runtime-validation.md`
- `docs/guarded-adapter-batch-http-grouping-validation.md`
- `docs/continuous-batching-implementation-plan.md`

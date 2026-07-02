# Guarded MLX Adapter-Batching Operator Guide

Experimental. Explicitly gated. Disabled by default. Smoke-harness validated.
Not production-ready.

## What It Is

Guarded MLX adapter batching is an explicitly gated experimental path for
compatible MLX text-only non-streaming requests. It groups compatible
requests through the guarded adapter-batch runner and preserves virtual
slot lifecycle, tombstones, controlled errors, metadata-only reporting,
and OpenAI-compatible response-shape checks.

## What It Is Not

Guarded MLX adapter batching is **not true token-step continuous batching**.
It does not implement shared decode-loop scheduling. It does not validate
HTTP queue/admission grouping. It is not enabled by default. It is not
production-ready. It does not claim latency or throughput improvement.

## Current Support Envelope

| Feature | Supported? |
|---|---|
| Backend | MLX only |
| Request type | Chat completion |
| Content | Text-only |
| Streaming | No |
| Tools | No |
| VLM/images | No |
| Group size | Controlled by flags |
| Token-step scheduler | No |
| Production ready | No |

## Enablement Flags

```bash
export WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_GUARDED_ADAPTER_BATCHING_MIN_GROUP_SIZE=2
export WHOOSHD_GUARDED_ADAPTER_BATCHING_MAX_GROUP_SIZE=2
export WHOOSHD_GUARDED_ADAPTER_BATCHING_MAX_TOKENS=128
export WHOOSHD_GUARDED_ADAPTER_BATCHING_TIMEOUT_SECONDS=30
```

**Both enablement flags are required.** One flag alone must not activate
guarded adapter batching.

## Disablement and Rollback

Normal disablement:
```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

Operators should disable flags first. Code revert is a release-management
action, not the normal rollback path.

## Validation Status

- Initial runtime validation: **inconclusive** (enabled live-path grouping not configured)
- Updated validation: **passed within smoke-harness scope** with `group_formed=true`

The updated result validates the smoke-harness path. It does not validate
HTTP queue/admission grouping or production queue behavior.

## How to Run the Smoke Harness

```bash
python scripts/smoke_guarded_mlx_adapter_batching_runtime.py
```

Expected:
```json
{"status":"passed","group_formed":true,"responses_ok":true,
 "response_shape_ok":true,"metadata_leak_detected":false,
 "production_ready":false,"performance_claim_made":false}
```

The smoke harness directly forms a compatible two-request group through
the runner. It is **not** an HTTP queue/admission grouping test.

## Metadata/Privacy Boundary

Smoke summaries and internal reports must not include: raw prompts, rendered
prompts, generated text, token IDs, slot IDs, tombstone IDs, sampling
signatures, tracebacks, KV handles, cache refs, model/tokenizer reprs.

Generated text may appear only in normal user-facing completion output.

## Claim Boundary

| Claim | Allowed? | Notes |
|---|---|---|
| Guarded MLX adapter batching exists | Yes | Experimental, gated |
| Smoke harness passes | Yes | Smoke-harness scope only |
| Production-ready | No | `production_ready=false` |
| Latency improvement | No | Not benchmarked |
| Throughput improvement | No | Not benchmarked |
| True token-step continuous batching | No | Future work |
| HTTP queue/admission grouping validated | No | Not validated |
| VLM batching | No | Unsupported |
| Streaming batching | No | Unsupported |

## Related Docs

- `docs/guarded-adapter-batch-runtime-validation.md`
- `docs/runtime-validation-results-guarded-adapter-batching-2026-07-02-updated.md`
- `docs/continuous-batching-implementation-plan.md`

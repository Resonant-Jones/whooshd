# Guarded Batching

How compatible work may ride together — under lock, key, and a very
stern clipboard. Not continuous batching. Not production-ready. No
dragon thunder in a jar.

## Purpose

Guarded batching is Whoosh'd's near-term adapter-batch path for
compatible local inference work. It groups eligible requests only
under explicit guard conditions, validation boundaries, and
disabled-by-default configuration.

Guarded batching is not true token-step continuous batching and does
not claim production readiness, latency improvement, or throughput
improvement.

## Current Status

Guarded adapter batching is implemented, validated in smoke-harness
and HTTP queue/admission grouping scopes, documented for operators,
and explicitly gated. It remains disabled by default and claim-bounded.

Token-step shared decode scheduling remains research-only for MLX
under the Cave Thunder decision.

## What Guarded Batching Is

- Adapter-level grouping path
- Explicitly gated capability
- Disabled-by-default feature
- Near-term MLX batching path
- Validation-scoped behavior
- Operator-bounded feature

Guarded batching groups compatible work only when configured guard
conditions allow it.

## What Guarded Batching Is Not

- Not true token-step continuous batching
- Not shared decode-loop scheduling
- Not MLX decode-loop ownership
- Not VLM batching / llama.cpp batching
- Not a benchmark result
- Not a latency/throughput claim
- Not production-ready
- Not enabled by default

## Why Guarded Batching Exists

True token-step shared decode scheduling requires backend primitives
that MLX does not currently expose. Guarded adapter batching provides
a safer near-term path while token-step scheduling remains
research-only. See [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md).

## Guarded Batching Lifecycle

```
request → admission → queue → compatibility check → guard evaluation →
adapter-batch path (or fallback) → runtime → response → observability
```

If guard conditions are not satisfied, guarded batching degrades to
the non-batched path rather than forcing grouping.

## Compatibility and Grouping

Compatibility dimensions: backend, model, adapter capability, request
shape, streaming posture, batch size, timeout, metadata safety, flags.

Compatibility is a guard condition, not a performance promise.

## Queue/Admission Relationship

Queue/admission controls whether requests proceed, wait, or be rejected.
Guarded batching may use admitted or queued compatible work only inside
explicit grouping boundaries. HTTP queue/admission grouping validation
passed under explicit guarded adapter-batch test conditions.
See [queue-and-admission.md](queue-and-admission.md).

## Scheduler Relationship

The scheduler may coordinate eligible work toward guarded grouping
where guard conditions and validation exist. Scheduler involvement
does not imply token-step shared decode. See [scheduler.md](scheduler.md).

## ThreadWake Separation

ThreadWake and guarded batching are separate subsystems. ThreadWake
concerns prefix reuse. Guarded batching concerns request grouping.
See [threadwake-prefix-cache.md](threadwake-prefix-cache.md).

## MLX Relationship

Guarded adapter batching is the near-term MLX batching path because
MLX does not expose lower-level primitives for Whoosh'd-owned
token-step shared decode. Cave Thunder decision:
[token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md).

## Token-Step Boundary

Guarded batching must not be described as token-step shared decode.
Fake backend contracts are architecture proofs, not MLX capability
proof. See [fake-token-step-scheduler-contract.md](fake-token-step-scheduler-contract.md).

## Configuration

`WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED`, `WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED`.
Related: `WHOOSHD_ENABLE_QUEUE`, `WHOOSHD_MAX_ACTIVE_REQUESTS`.

Flags do not imply production readiness or performance improvement.
Guarded batching is disabled by default and must be explicitly enabled.

## Validation Coverage

- Guarded adapter batching tests
- HTTP queue/admission grouping validation
- Batching arc closeout tests
- Cave Thunder decision tests
- Scheduler/queue/ThreadWake docs tests

Validation is scoped evidence — no production/performance implication.

## Observability and Privacy

Metadata-only. Forbidden: raw prompts, rendered prompts, generated text,
token IDs, slot/tombstone IDs, sampling signatures, KV handles, cache
refs, model/tokenizer reprs, tracebacks, raw exceptions.

## Rollback

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

Disablement must not require changing queue, scheduler, or runtime
behavior.

## Operator Claim Boundaries

| Claim | Status |
|---|---|
| Guarded adapter batching exists | Allowed |
| Scoped validation passed | Allowed, scoped |
| HTTP grouping validated | Allowed, scoped |
| Disabled by default | Allowed |
| Production-ready | Not allowed |
| Latency/throughput improvement | Not claimed |
| True continuous batching | Not allowed |
| Token-step scheduling implemented | Not allowed |

## Non-Goals

New batching behavior, default enablement, production certification,
continuous batching, token-step scheduling, MLX decode-loop ownership,
VLM/llama.cpp batching, benchmark/latency/throughput claims.

## Related Docs

- [guarded-adapter-batching-operator-guide.md](guarded-adapter-batching-operator-guide.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)
- [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md)
- [queue-and-admission.md](queue-and-admission.md)
- [scheduler.md](scheduler.md)
- [threadwake-prefix-cache.md](threadwake-prefix-cache.md)

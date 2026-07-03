# True Token-Step Shared Decode Scheduler Research

Research only. No implementation. No production claim.

## Purpose

Defines what Whoosh'd would need to own, prove, and validate before
implementing true token-step shared decode scheduling. This is a
different beast from guarded adapter batching — the scheduler owns
decode steps, per-request sampling inside a shared loop, cancellation
mid-loop, timeout mid-loop, demux, and cleanup.

## Current State

Guarded adapter batching is implemented and validated for compatible
MLX text-only non-streaming requests. It uses guarded grouping,
adapter-batch execution, virtual slots, tombstones, controlled errors,
and metadata-only reporting. It is **not** true token-step continuous
batching and does not implement shared decode-loop scheduling.

HTTP queue/admission grouping has been validated under explicit
guarded adapter-batch test conditions. This closes the operator-facing
adapter-batch caveat but does not prove token-step shared decode
scheduling.

## Adapter Batch vs Token-Step Shared Decode

| Capability | Guarded adapter batching | Token-step shared decode |
|---|---|---|
| Groups requests | Yes | Yes |
| Owns decode loop | No | Yes or coordinated explicitly |
| Per-token scheduling | No | Yes |
| Per-request sampler state inside shared loop | No | Required |
| Streaming demux | No | Required |
| Mid-loop cancellation | No | Required |
| Backend KV/slot cleanup | Virtual only | Backend-verified required |
| Production-ready | No | No |
| Performance claim | No | No |

## What Whoosh'd Must Own

True token-step shared decode requires ownership or explicit coordination of:

- Request admission
- Prefill scheduling
- Decode-step scheduling
- Sequence state
- Backend slot ownership
- KV/cache lifecycle
- Per-request sampling state
- Token/chunk demux
- Finish detection
- Stop condition handling
- Cancellation
- Timeouts
- Failure isolation
- Cleanup
- Metadata-only observability

**If Whoosh'd cannot own or observe these primitives safely, it must not
claim true token-step shared decode scheduling.**

## Required Backend Primitives

| Primitive | Required? | Risk if missing | Validation needed |
|---|---|---|---|
| Prefill/decode split | Yes | Cannot schedule token steps | Backend spike |
| Per-sequence handle | Yes | Cannot demux or clean up | Slot ownership test |
| Per-request sampler state | Yes | Sampling bleed | Isolation test |
| Cancellation hook | Yes | Stuck jobs | Cancellation smoke |
| Timeout hook | Yes | Stuck jobs | Timeout smoke |
| Cleanup hook | Yes | Leaked KV/cache | Cleanup probe |
| Stream demux | Yes | Wrong output routing | Demux test |

## Runtime State Machine

```
ADMITTED → PREFILL_QUEUED → PREFILLING → READY_TO_DECODE → DECODING → EMITTING → FINISHED
                                                                          ↓
                                                          CANCELLED / FAILED / TIMED_OUT
```

Terminal states: FINISHED, CANCELLED, FAILED, TIMED_OUT, CLEANED_UP.

Every sequence must reach exactly one terminal user-visible state
and exactly one cleanup state.

## Failure Isolation

Failure classes: single-sequence, shared-step, backend batch, sampling,
demux, cleanup, timeout, cancellation.

Required invariant: a failure must never silently route one request's
output to another request.

## Streaming and Demux

Required for: non-streaming aggregation, streaming chunks, finish events,
usage events, error events, cancellation events, timeout events.

Public streaming support must not be added until internal demux proves
per-request routing, terminal events, cancellation, timeout, and cleanup.

## Queue/Admission Relationship

The queue/admission layer decides which requests are eligible to enter
the scheduler. The token-step scheduler decides which active sequences
participate in each decode step. HTTP queue/admission grouping
validation does not prove token-step scheduling.

## Backend Capability Matrix

| Backend | Prefill/decode split | Per-sequence handle | Sampler isolation | Cancellation | Cleanup | Whoosh'd owns loop |
|---|---|---|---|---|---|---|
| Fake | Possible | Possible | Possible | Possible | Possible | Yes |
| MLX | Needs spike | Needs spike | Needs spike | Needs spike | Needs spike | Needs spike |
| llama.cpp | Server-owned | Server-owned | Server-owned | Server-owned | Server-owned | Likely no |

## Risk Ledger

| Risk | Severity | Mitigation |
|---|---|---|
| Sampling bleed between requests | High | Per-request state isolation |
| Token/chunk misrouting | High | Demux validation |
| KV/cache leaks | High | Cleanup probes |
| Sequence cleanup failure | Moderate | Idempotent cleanup |
| Cancellation affecting peers | High | Per-sequence cancellation |
| Timeout affecting peers | High | Per-sequence timeout |
| Metadata leakage | High | Metadata-only snapshots |
| False performance claims | Moderate | No claims without benchmarks |
| Production-readiness overclaim | High | Hard boundary enforcement |

## Validation Ladder

1. Fake backend token-step scheduler contract
2. Fake backend per-request sampling isolation
3. Fake backend cancellation and timeout isolation
4. Fake backend failure isolation
5. Fake backend demux and terminal-event proof
6. MLX decode-step ownership spike
7. MLX per-sequence handle proof
8. MLX cancellation/timeout proof
9. MLX cleanup proof
10. MLX internal token-step prototype
11. MLX guarded live token-step prototype
12. Runtime validation packet
13. Operator docs and claim-boundary update

## Recommended Next Step

**Fake backend token-step scheduler contract** — build a contract in the
sandbox that proves sequence state, per-step scheduling, demux,
cancellation, timeout, failure isolation, and cleanup without requiring
MLX decode-loop ownership.

Branch: `codex/fake-token-step-scheduler-contract`. Research only. No
implementation. No production claim. No performance claim.

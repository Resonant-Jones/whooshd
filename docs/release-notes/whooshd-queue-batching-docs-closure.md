# Whoosh'd Queue, Batching, Token-Step Research, and Docs Closure

Release-facing digest. Proof-backed. No marketing fog.

## Summary

Whoosh'd now has a documented queue/admission foundation, scheduler
path, ThreadWake/prefix-cache guidance, guarded adapter batching
documentation, runtime validation guidance, batching arc closeout,
token-step research closure, and a navigable documentation spine.

Guarded adapter batching is the near-term MLX batching path.
Token-step shared decode scheduling remains research-only for MLX
under the Cave Thunder decision.

This digest does not claim production-ready batching, latency
improvement, throughput improvement, true continuous batching,
or MLX token-step scheduling.

## What Changed

### Queue and Admission
Documented FIFO, active jobs, config, scheduler handoff, guarded
batching relationship, HTTP grouping validation, failure boundaries.

### Scheduler
Documented responsibilities, queue/admission relationships,
cache-aware posture, ThreadWake posture, Cave Thunder boundary.

### ThreadWake / Prefix-Cache
Documented as prompt-prefix reuse and runtime optimization. Not AI
memory, not persistent recall, not a speed claim.

### Guarded Batching
Documented as disabled-by-default, explicitly gated, near-term
adapter-batch path under scoped validation.

### Token-Step Research
Researched, fake-proven, MLX-blocked, decision-recorded as Cave Thunder.

### Runtime Validation
Documented as scoped evidence with result meanings, packet structure,
backend-specific scope, and operator claim limits.

### Documentation
Spine built, six subsystem deep dives, boundary tests, batching arc
closeout, Cave Thunder decision, docs pass closeout.

## What Is Safe to Claim

| Claim | Status |
|---|---|
| Queue/admission docs exist | Safe-current |
| Scheduler docs exist | Safe-current |
| ThreadWake is prompt-prefix reuse | Safe-current |
| Guarded adapter batching documented | Safe-current |
| Guarded batching has scoped validation | Safe-qualified |
| HTTP grouping validation passed | Safe-qualified |
| Token-step research completed | Safe-current |
| Fake contracts prove scheduler shape in sandbox | Safe-qualified |
| MLX token-step remains research-only | Safe-current |
| Runtime validation is scoped evidence | Safe-current |
| Docs anatomy pass complete | Safe-current |

## What Remains Experimental

Guarded adapter batching, HTTP grouping under guarded conditions,
ThreadWake behavior where backend support varies, cache-aware
scheduling posture.

Experimental means explicitly bounded, configured, validated under
recorded conditions, and subject to rollback.

## What Remains Research-Only

True token-step shared decode for MLX, Whoosh'd-owned MLX decode loop,
selective MLX decode-step ownership, stream demux for shared decode,
durable ThreadWake snapshots, performance claims without benchmarks.

## Operator Notes

Start with [docs/README.md](../README.md). Use [operator-guide.md](../operator-guide.md).
Check [runtime-validation.md](../runtime-validation.md) before treating
runtime results as evidence. See [guarded-batching.md](../guarded-batching.md)
before enabling guarded batching.

Do not enable experimental paths without checking configuration,
validation, and claim-boundary docs.

## Developer Notes

New features require validation docs before operator-facing claims.
New batching changes require guarded validation or benchmarks before
stronger claims. New token-step claims require backend primitive
validation. New docs should include claim-boundary tests.

## Evidence Map

| Area | Evidence |
|---|---|
| Batching arc | [batching-arc-closeout-digest.md](../batching-arc-closeout-digest.md) |
| Cave Thunder | [token-step-cave-thunder-decision.md](../token-step-cave-thunder-decision.md) |
| Docs pass | [documentation-pass-closeout-digest.md](../documentation-pass-closeout-digest.md) |
| Queue/admission | [queue-and-admission.md](../queue-and-admission.md) |
| Scheduler | [scheduler.md](../scheduler.md) |
| ThreadWake | [threadwake-prefix-cache.md](../threadwake-prefix-cache.md) |
| Guarded batching | [guarded-batching.md](../guarded-batching.md) |
| Runtime validation | [runtime-validation.md](../runtime-validation.md) |

## Claim Boundaries (Do Not Use)

| Blocked claim | Reason |
|---|---|
| Production-ready batching | Not validated |
| Latency/throughput improvement | Requires benchmarks |
| True continuous batching | Not implemented |
| MLX token-step implemented | Research-only |
| Fake backend proves MLX | Sandbox-only |
| ThreadWake is AI memory | Prefix reuse, not memory |
| Validation certifies production | Scoped evidence only |

## Related

- [Claim Ledger](whooshd-queue-batching-docs-claim-ledger.md)
- [Batching Arc Closeout](../batching-arc-closeout-digest.md)
- [Cave Thunder Decision](../token-step-cave-thunder-decision.md)
- [Docs Pass Closeout](../documentation-pass-closeout-digest.md)

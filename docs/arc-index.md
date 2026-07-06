# Arc Index

History of completed engineering arcs in Whoosh'd.

## Queue and FIFO Arc

Bounded FIFO queue with admission control, timeout, cancellation, and
live smoke validation.

## ThreadWake / Prefix-Cache Arc

Observe-mode metadata, fake reuse proof, route bridge, tokenizer
fidelity, KV skeleton, MLX smoke.

## Scheduler Skeleton Arc

FIFO scheduler default, cache-aware experimental policy with fairness
bypass limit.

## Batching Feasibility Arc

Feasibility analysis, execution skeleton, live-path stub batching,
hardening, real backend feasibility probe, MLX batch smoke.

## Guarded Adapter Batching Arc

Implementation, smoke-harness validation, HTTP queue/admission grouping
validation, operator docs, closeout digest. Closed with PR #59.

## Token-Step Shared Decode Research Arc

Research, fake backend scheduler contract, fake isolation contracts,
MLX decode-step ownership spike (Cave Thunder), decision packet.
Closed with PR #58.

## Current State

```
Adapter batching: built, validated, documented.
Token-step scheduling: researched, fake-proven, MLX-blocked, decision recorded.
```

See [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md).

## Documentation Anatomy Pass

The documentation anatomy pass added the docs spine, queue/admission
deep dive, scheduler deep dive, ThreadWake / prefix-cache deep dive,
guarded batching deep dive, runtime validation deep dive, and
documentation pass closeout digest.

See [documentation-pass-closeout-digest.md](documentation-pass-closeout-digest.md).

## Release-Facing Closure

The release-facing closure digest summarizes what changed, what is
safe to claim, what remains experimental, what remains research-only,
and where the evidence lives.

See [release-notes/whooshd-queue-batching-docs-closure.md](release-notes/whooshd-queue-batching-docs-closure.md)
and [release-notes/whooshd-queue-batching-docs-claim-ledger.md](release-notes/whooshd-queue-batching-docs-claim-ledger.md).

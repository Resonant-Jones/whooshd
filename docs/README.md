# Whoosh'd Documentation

Local-first inference orchestration: queueing, scheduling, runtime
validation, model routing, guarded batching, and operator-safe local
model serving.

## Closure Digests

Start here for current state, safe claims, experimental areas, and evidence:

- [Release-facing closure](release-notes/whooshd-queue-batching-docs-closure.md)
- [Claim ledger](release-notes/whooshd-queue-batching-docs-claim-ledger.md)
- [Documentation pass closeout](documentation-pass-closeout-digest.md)
- [Batching arc closeout](batching-arc-closeout-digest.md)
- [Cave Thunder decision](token-step-cave-thunder-decision.md)

## Start Here

- **Operators**: [Operator Guide](operator-guide.md)
- **Developers**: [Developer Guide](developer-guide.md)
- **Architecture**: [Architecture Overview](architecture.md)
- **Operations and security**: [Whoosh'd Logging Safety](security/whooshd-logging-safety.md)

## Reference

- [Subsystems](subsystems.md)
- [Glossary](glossary.md)
- [Validation Index](validation-index.md)
- [Arc Index](arc-index.md)

## Key Boundaries

Whoosh'd does not claim production-ready continuous batching, latency
improvement, or throughput improvement unless a specific validation
packet and benchmark packet say so.

Guarded adapter batching is experimental, explicitly gated, disabled
by default, and validated within scoped test conditions.

Whoosh'd-owned log records are bounded by the [logging safety contract](security/whooshd-logging-safety.md);
historical files, platform logs, and external collectors are not claimed to
be retroactively scrubbed.

Token-step shared decode scheduling remains research-only for MLX
under the current integration.

## Current State

| System | Status |
|---|---|
| Guarded adapter batching | Built, validated, documented |
| Token-step shared decode | Researched, fake-proven, MLX-blocked |
| Queue and admission | Implemented |
| Scheduler | Implemented (FIFO + cache-aware) |
| ThreadWake | Observe-mode, KV skeleton |
| MLX runtime | Implemented |

## Batching Arc

The batching arc closed with [PR #59](batching-arc-closeout-digest.md).
See the [arc index](arc-index.md) for the full trail.

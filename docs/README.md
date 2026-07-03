# Whoosh'd Documentation

Local-first inference orchestration: queueing, scheduling, runtime
validation, model routing, guarded batching, and operator-safe local
model serving.

## Start Here

- **Operators**: [Operator Guide](operator-guide.md)
- **Developers**: [Developer Guide](developer-guide.md)
- **Architecture**: [Architecture Overview](architecture.md)

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

# Subsystems

## Queue and Admission

Controls which requests enter execution under configured limits.
Supports enable/disable, bounded depth, timeout, and cancellation.
[queue-policy.md](queue-policy.md)

## Scheduler

Selects next request from queue. FIFO default, cache-aware experimental.
[scheduler.md](scheduler.md)

## ThreadWake / Prefix Cache

Observes and analyzes prompt-prefix reuse opportunities. KV skeleton
for future cache materialization. [threadwake-mlx-kv-feasibility.md](threadwake-mlx-kv-feasibility.md)

## Cache-Aware Scheduling

Prefers ThreadWake cache-ready candidates bounded by fairness bypass
limit. [scheduler.md](scheduler.md)

## Runtime Validation

Validation packet system for proving runtime behavior under explicit
flags. [validation-index.md](validation-index.md)

## Model Registry

Declares available models, formats, engines, and capabilities.
[models.yaml](../configs/models.yaml)

## MLX Runtime

In-process and subprocess-supervised MLX adapter. Supports streaming,
non-streaming, KV skeleton, tokenizer fidelity, and batch generation.

## Guarded Adapter Batching

Explicitly gated MLX adapter-batch path for compatible text-only
non-streaming requests. [guarded-adapter-batching-operator-guide.md](guarded-adapter-batching-operator-guide.md)

## Token-Step Shared Decode Research

Researched, fake-proven, MLX-blocked. Decision recorded as Cave Thunder.
[token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md)

# Scheduler

The decision layer that coordinates eligible work after admission.
Orders work, respects FIFO, supports cache-awareness, and preserves
claim boundaries.

## Current Status

The scheduler has a tested skeleton and participates in queue/admission
and batching-related validation. It supports the documented guarded
adapter-batch path and related validation boundaries, but does not
implement true token-step shared decode scheduling.

Token-step shared decode scheduling remains research-only for MLX
under the Cave Thunder decision.

## Scheduler Responsibilities

- Ordering eligible work (FIFO default, cache-aware experimental)
- Coordinating with queue/admission
- Respecting active job limits
- Supporting guarded adapter-batch grouping boundaries
- Keeping observability metadata-safe
- Preserving claim boundaries

## What the Scheduler Does Not Own

- Model generation internals
- MLX decode-loop ownership
- Token-step shared decode scheduling
- Backend KV/cache lifecycle
- Public streaming demux
- Production performance claims

The scheduler can coordinate work, but it does not make MLX token-step
scheduling possible unless the backend exposes the required lower-level
primitives.

## Request Lifecycle

```
HTTP request → admission → queue → scheduler ordering → runtime → response
```

## Queue/Admission Relationship

Queue/admission decides whether a request can proceed immediately, wait,
or be rejected. The scheduler coordinates eligible work after that
admission boundary. See [queue-and-admission.md](queue-and-admission.md).

## FIFO Relationship

FIFO behavior belongs to the queue/admission contract. Scheduler behavior
must not silently violate documented FIFO expectations without a specific
policy and validation coverage.

## ThreadWake / Prefix-Cache Relationship

ThreadWake and prefix-cache awareness may inform scheduling decisions,
but cache-aware scheduling must remain explicitly validated and must not
be used to claim latency or throughput improvement without benchmark
evidence.

## Cache-Aware Scheduling

Cache-aware scheduling can rank or group work based on reusable context
signals only where the relevant contracts and validation exist. It must
not leak prompt text, token IDs, cache handles, or internal object
representations in reports.

## Guarded Adapter-Batch Relationship

Guarded adapter batching is the near-term MLX batching path. Scheduler
and admission behavior may help form compatible groups under explicit
guarded adapter-batch conditions, but guarded adapter batching remains
explicitly gated, disabled by default, not production-ready, and makes
no latency or throughput claim.

HTTP queue/admission grouping validation passed under explicit guarded
adapter-batch test conditions.

## Token-Step Shared Decode Boundary

The scheduler deep dive must not imply that true token-step shared decode
scheduling is implemented. Token-step shared decode remains research-only
for MLX under the Cave Thunder decision because MLX decode-step ownership
is blocked under the current integration.

Fake backend token-step scheduler contracts are architecture proofs, not
MLX capability proof.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable queue |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` | Max active jobs |
| `WHOOSHD_SCHEDULER_POLICY` | `fifo` | Select scheduler ordering policy; valid values are `fifo` and experimental `cache_aware_fifo` |
| `WHOOSHD_SCHEDULER_MAX_BYPASS` | `1` | Maximum times cache-aware scheduling may bypass a queued request before FIFO fairness forces it forward |
| `WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED` | `false` | Enable guarded adapter batching; this is a separate batching gate and does not select scheduler policy |
| `WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED` | `false` | Enable MLX guarded adapter batching; this is a separate MLX batching gate and does not select scheduler policy |

Use `WHOOSHD_SCHEDULER_POLICY=cache_aware_fifo` to opt into the
experimental cache-aware scheduler. Guarded adapter-batching flags do not
enable cache-aware scheduling or change the scheduler fairness bypass
limit.

Configuration flags do not imply production readiness or performance
improvement.

## Failure and Timeout Boundaries

Scheduler-facing failures must resolve to controlled terminal behavior
and metadata-safe observability. Timeout behavior must not be used to
imply backend-native cancellation unless explicitly validated.

Fake backend contracts prove cancellation, timeout, and failure
isolation in a sandbox only. They do not prove MLX backend-native
isolation.

## Observability and Privacy

Scheduler reports and validation summaries must be metadata-only.
Forbidden: raw prompts, rendered prompts, generated text in reports,
token IDs, slot IDs, tombstone IDs, sampling signatures, KV handles,
cache refs, model/tokenizer reprs, tracebacks, raw exception messages.

## Validation Coverage

- Queue/admission tests (`tests/test_queue.py`)
- Scheduler tests (`tests/test_scheduler.py`)
- Guarded adapter-batch tests (`tests/test_guarded_mlx_adapter_batching.py`)
- HTTP grouping validation (`tests/test_guarded_adapter_batch_http_grouping_validation.py`)
- Fake token-step contracts (`tests/test_fake_token_step_*.py`)
- Cave Thunder tests (`tests/test_token_step_cave_thunder_decision.py`)
- Docs boundary tests (`tests/test_guarded_adapter_batch_operator_docs.py`)

Validation coverage is scoped evidence. It does not imply production
readiness, latency improvement, or throughput improvement.

## Non-Goals

- Production scheduler rewrite
- True token-step shared decode scheduling implementation
- MLX decode-loop ownership
- Public streaming demux
- VLM batching
- llama.cpp Whoosh'd-owned batching
- Benchmark reporting
- Latency/throughput claims
- Production-readiness claims

## Related Docs

- [queue-and-admission.md](queue-and-admission.md)
- [guarded-adapter-batching-operator-guide.md](guarded-adapter-batching-operator-guide.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)
- [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md)
- [fake-token-step-scheduler-contract.md](fake-token-step-scheduler-contract.md)
- [fake-token-step-isolation-contracts.md](fake-token-step-isolation-contracts.md)
- [validation-index.md](validation-index.md)
- [subsystems.md](subsystems.md)

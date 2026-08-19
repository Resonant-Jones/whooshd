# Capacity Controller

The capacity controller is the throughput control plane's brain.
It answers one question:

> **How many requests may execute right now?**

It does **not** own inference, scheduling policy, queue selection, or
backend execution.  It is a thin, deterministic decision layer.

## Modes

### `fixed` (default)

When `WHOOSHD_CAPACITY_MODE` is unset or set to `fixed`, the
controller's effective active limit is **exactly**
`WHOOSHD_MAX_ACTIVE_REQUESTS`.  No profile is consulted.  This is the
pre-throughput behaviour — operators who do nothing see no change.

### `adaptive`

When `WHOOSHD_CAPACITY_MODE=adaptive` and `WHOOSHD_CAPACITY_PROFILE_PATH`
points to a structured `CapacityProfile` JSON, the controller may
**lower** the effective active limit below the operator ceiling.

The profile is eligible only when its model ID, runtime, machine class, and
host-memory identity exactly match the configured runtime identity. Set that
identity with `WHOOSHD_CAPACITY_MODEL_ID`, `WHOOSHD_CAPACITY_RUNTIME`, and
`WHOOSHD_CAPACITY_MACHINE_CLASS`. A missing or mismatched identity rejects the
calibration and falls back to the operator ceiling; evidence from a different
model or host is never applied.

Adaptive mode **never exceeds** the operator ceiling.  The operator
ceiling is a hard upper bound.

## Inputs

| Input | Source |
|-------|--------|
| `operator_ceiling` | `WHOOSHD_MAX_ACTIVE_REQUESTS` |
| `mode` | `WHOOSHD_CAPACITY_MODE` (fixed / adaptive) |
| `calibrated_concurrency` | `CapacityProfile.recommended_active_concurrency` |
| `memory_pressure` | `RuntimeState.memory.pressure` |
| `active_jobs` | `RuntimeState.active_jobs` |
| `queue_depth` | `RuntimeState.queue_depth` |
| `max_queue_depth` | `WHOOSHD_MAX_QUEUE_DEPTH` |
| `runtime_ready` | `RuntimeState.model_lifecycle` |
| configured model identity | `WHOOSHD_CAPACITY_MODEL_ID` |
| configured runtime identity | `WHOOSHD_CAPACITY_RUNTIME` |
| configured machine class | `WHOOSHD_CAPACITY_MACHINE_CLASS` |

## Outputs

`evaluate()` returns a :class:`CapacityEvaluation`:

| Field | Meaning |
|-------|---------|
| `action` | `run` / `queue` / `reject` |
| `effective_active_limit` | current active limit |
| `operator_ceiling` | hard upper bound |
| `mode` | `fixed` / `adaptive` |
| `reason` | structured enum (see below) |
| `calibrated_concurrency` | from profile (if loaded) |
| `memory_pressure` | observed value |
| `profile_loaded` | whether a profile was found |
| `profile_eligible` | whether the profile identity matches this runtime |
| `profile_rejection_reason` | mismatch reason when the profile is ineligible |

### Decision reasons

| Reason | Meaning |
|--------|---------|
| `below_operator_ceiling` | Under the operator ceiling; admit. |
| `calibrated_limit` | Adaptive profile lowers the effective limit; admit up to that. |
| `operator_ceiling` | Adaptive profile does not lower; admit up to operator ceiling. |
| `missing_profile` | Adaptive mode requested but no profile found; conservatively fall back to operator ceiling. |
| `invalid_profile` | Profile JSON exists but failed validation; same fallback. |
| `memory_pressure_high` | Under high memory pressure; deny new admission. |
| `queue_full` | Queue is at capacity; reject. |
| `runtime_not_ready` | Runtime is not ready; reject. |
| `batch_active_limit_reached` | Reserved for batch interaction. |

## Snapshot

`GET /runtime/capacity` returns the controller's snapshot.  No prompt
content, generated text, KV handles, or token IDs are exposed.

```json
{
  "capacity_mode": "fixed",
  "effective_active_limit": 2,
  "operator_ceiling": 2,
  "active_jobs": 2,
  "queue_depth": 1,
  "max_queue_depth": 8,
  "capacity_reason": "calibrated_limit",
  "memory_pressure": "normal",
  "calibrated_concurrency": null,
  "profile_loaded": false,
  "profile_eligible": false,
  "profile_rejection_reason": null,
  "runtime_ready": true,
  "model": "stub-model",
  "runtime": "stub",
  "scheduler": {
    "policy": "fifo",
    "last_decision_reason": "fifo_oldest",
    "eligible_count": 1,
    "cache_affinity_candidates": 0,
    "fairness_bypasses": 0,
    "max_bypass": 1
  }
}
```

## Capacity profile format

A `CapacityProfile` is a JSON document with this shape (see
`whooshd.capacity_profile`):

```json
{
  "model_id": "mlx-community/gemma-4-12b-it-qat-4bit",
  "runtime": "mlx_lm_server",
  "machine_class": "darwin-arm64",
  "host_memory_bytes": 34359738368,
  "quantization": "affine-4bit-group64",
  "prompt_size_chars": 1024,
  "configured_max_tokens": 256,
  "streaming": true,
  "bands": [
    {"concurrency": 1, "successful": true,  "ttft_p50_ms": 250,  "latency_p95_ms": 1800, "success_count": 8},
    {"concurrency": 2, "successful": true,  "ttft_p50_ms": 260,  "latency_p95_ms": 2000, "success_count": 8},
    {"concurrency": 3, "successful": true,  "ttft_p50_ms": 320,  "latency_p95_ms": 2800, "success_count": 8},
    {"concurrency": 4, "successful": false, "stuck_count": 2,    "failure_count": 1, "notes": ["OOM after 4 concurrent"]}
  ],
  "recommended_active_concurrency": 3
}
```

The profile is produced by `python -m whooshd.bench.capacity_bench`.
The CLI accepts an immutable `--profile-model-id`, `--machine-class`, and
optional `--quantization` so the artifact cannot be confused with a public
alias or another host. Failure to load or match the profile is non-fatal — the
controller falls back to the operator ceiling.

## What is **not** in this controller

* No inference policy.  The adapter picks its own batching.
* No KV cache accounting.  ThreadWake seam only.
* No continuous batching.  Separate milestone.
* No prompt-prefix awareness.  Cache-aware FIFO is in the scheduler,
  not the capacity controller.

## Backward compatibility

* Default mode is `fixed`.  No env var set = current behaviour.
* Existing `WHOOSHD_MAX_ACTIVE_REQUESTS` semantics preserved.
* Existing structured 429 responses preserved.
* Runtime semaphores are unchanged.
* No request can bypass model/runtime health checks.
* No automatic calibration may intentionally induce system-wide memory
  exhaustion.

## Glossary of related terms

These four terms are **not interchangeable**:

* **Client concurrency** — outstanding HTTP requests Whoosh'd has accepted.
* **Active inference concurrency** — requests simultaneously executing against
  a runtime adapter.
* **Batch size** — number of compatible requests executed in one backend
  batch call.
* **Queue depth** — requests accepted but waiting for execution capacity.

See `docs/glossary.md`.

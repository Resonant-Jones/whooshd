# Operator Guide

How to start, configure, inspect, validate, and roll back Whoosh'd.

This guide covers **operating** the system. Design rationale lives in the
subsystem documents it links to.

## Starting

```bash
whoosh --host 127.0.0.1 --port 8000     # foreground
whoosh -d                               # background daemon
whoosh status
whoosh logs
whoosh down
```

Developer/debug startup:

```bash
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

The CLI tracks one daemon process at a time via `~/.whooshd/whooshd.pid` and
`~/.whooshd/whooshd.log`. Custom `--port` values work for startup and status
probes, but PID and log state are global, not per-port. `whoosh down --port
8010` stops the one tracked daemon, not a separate per-port instance.

The CLI will not kill unknown processes occupying a port. Inspect first:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

## Core Configuration

See `whooshd/config.py` for the authoritative list. The ones operators change
most:

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ADAPTER` | `stub` | `stub`, `mlx`, `llama_cpp` |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` | Active request admission limit and capacity operator ceiling |
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable the bounded FIFO queue |
| `WHOOSHD_MODEL_REGISTRY_PATH` | none | Explicit authoritative runtime registry |

### Capacity control plane

`fixed` mode is the default and is exactly `WHOOSHD_MAX_ACTIVE_REQUESTS` — no
profile is consulted. `adaptive` mode may only **lower** the effective active
limit from a validated `CapacityProfile`.

```bash
WHOOSHD_CAPACITY_MODE=adaptive
WHOOSHD_CAPACITY_PROFILE_PATH=/path/to/capacity-profile.json
WHOOSHD_CAPACITY_MODEL_ID=<immutable model identity>
WHOOSHD_CAPACITY_RUNTIME=<exact runtime identity>
WHOOSHD_CAPACITY_MACHINE_CLASS=<stable host class>
```

A profile is eligible only on an exact match of model id, runtime, machine
class, and host memory. A missing, invalid, or mismatched profile is non-fatal:
Whoosh'd falls back to the operator ceiling and records `missing_profile`,
`invalid_profile`, or `model_mismatch`. Evidence calibrated for one model or
host is never applied to another.

Generate a profile against a running instance:

```bash
python -m whooshd.bench.capacity_bench \
  --base-url http://127.0.0.1:8000 \
  --model <model> \
  --profile-model-id <immutable-model-id> \
  --machine-class <host-class> \
  --band 1 2 3 4 \
  --output capacity-profile.json
```

**Rollback:** `unset WHOOSHD_CAPACITY_MODE WHOOSHD_CAPACITY_PROFILE_PATH`
returns the process to fixed/default behavior.

### Queue and admission

By default, over-capacity requests receive a structured `429
runner_overloaded`. The bounded FIFO queue is opt-in:

```bash
WHOOSHD_ENABLE_QUEUE=true
WHOOSHD_MAX_QUEUE_DEPTH=8
WHOOSHD_QUEUE_TIMEOUT_SECONDS=120
```

Queueing absorbs small local bursts instead of making the caller retry. It is
disabled by default; enable it intentionally. See
[Queue and Admission](queue-and-admission.md).

## Inspecting a Running Daemon

| Question | Endpoint |
|---|---|
| Is the process alive? | `GET /health` |
| Can it serve inference right now? | `GET /ready` |
| What is the full runtime state? | `GET /runtime` |
| What is the model lifecycle? | `GET /runtime/model` |
| Why was my request rejected? | `GET /runtime/admission` |
| How many requests may execute now? | `GET /runtime/capacity` |
| What is in flight? | `GET /runtime/requests` |
| What is ThreadWake observing? | `GET /runtime/threadwake/analysis` |

All of these are metadata-only. None expose prompts, messages, generated text,
token IDs, KV handles, or credentials.

```bash
curl -s http://127.0.0.1:8000/runtime/capacity | python3 -m json.tool
curl -s http://127.0.0.1:8000/runtime/admission | python3 -m json.tool
```

## Request Identity

Two identities are always distinct:

- `X-Request-ID` — yours, echoed back. Accepted only when 1–128 characters
  from `A-Za-z0-9._:-`.
- `X-Whoosh-Request-ID` — Whoosh'd's lifecycle identity, `whoosh-` prefixed.

Cancel by the **Whoosh'd** id:

```bash
curl -X POST http://127.0.0.1:8000/runtime/requests/<whoosh-request-id>/cancel
```

The cancellation response returns the associated correlation pair. See
[Control-Plane Contract v1](control-plane-v1.md).

## Validation

Treat a validation result as scoped evidence, not production readiness. See
[Validation Index](validation-index.md) for current pass/fail/blocked/pending
status, and [Manual Runtime Validation](manual-runtime-validation.md) for how to
produce a new packet.

Smoke checks against a running stub:

```bash
sh scripts/smoke_stub.sh
sh scripts/smoke_openai_compat.sh
sh scripts/smoke_threadwake.sh
```

## Diagnosing

| Symptom | Check |
|---|---|
| Server not responding | `whoosh status` or `curl /health` |
| `/ready` returns 503 | Model warming, unloaded, failed, or degraded — check `/runtime/model` |
| MLX lane offline | Confirm `mlx_lm.server` is running at `WHOOSHD_MLX_HOST:PORT` |
| Warmup hangs | Check the model path exists; MLX may download on first load |
| 429 too many requests | At capacity — lower concurrency or enable the queue intentionally |
| 429 with `contract_version_unsupported` | Send `whooshd.control.v1` or omit the header |
| Model listed but not runnable | Check adapter registration and `/health/runtime` |
| Concurrency lower than configured | `/runtime/capacity` — check `capacity_reason` and profile eligibility |
| ThreadWake analysis all zeros | Normal when off or when no candidates exist; enable observe mode first |
| Port already in use | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |

## What Not to Claim

- Guarded adapter batching is **not production-ready**, is not token-step
  continuous batching, and claims no latency or throughput improvement.
- Token-step shared decode scheduling is **research-only for MLX** under the
  current integration.
- Durable KV snapshots are **deferred**. Production ThreadWake KV reuse is
  **not enabled** — backends report `unsupported` capability by default.
- A capacity profile is bound to a specific model, runtime, machine class, and
  host memory. It is not a portable concurrency recommendation.
- Live deployment claims require a rehearsal against the target runtime and
  machine. Passing tests alone do not certify a deployment.

See [Batching Arc Closeout](batching-arc-closeout-digest.md),
[Cave Thunder Decision](token-step-cave-thunder-decision.md), and
[ThreadWake](threadwake/README.md).

## Rollback

Every experimental or gated surface is disabled by default and rolls back by
unsetting its flag:

| Surface | Rollback |
|---|---|
| Capacity adaptive mode | `unset WHOOSHD_CAPACITY_MODE WHOOSHD_CAPACITY_PROFILE_PATH` |
| Bounded FIFO queue | `unset WHOOSHD_ENABLE_QUEUE` |
| Guarded adapter batching | `unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED` |
| ThreadWake | `WHOOSHD_THREADWAKE_ENABLED=false` |
| Experimental MLX KV | `unset WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL` |

## Related

- [API Reference](api-reference.md)
- [Capacity Controller](capacity-controller.md)
- [Control-Plane Contract v1](control-plane-v1.md)
- [Request and Backend Boundary](request-contract.md)
- [Queue and Admission](queue-and-admission.md)
- [Logging Safety Contract](security/whooshd-logging-safety.md)
- [Local launchd Runtime](ops/whooshd-launchd-local-runtime.md)

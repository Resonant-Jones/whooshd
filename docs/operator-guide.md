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

This limits running **several instances on one host**. It is not a
multi-machine limitation: two installations on different hosts already have
separate `~/.whooshd` directories, and their PID files do not conflict.

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

**Two ceilings, and the lower one is real.** The broker's admission limit
(`WHOOSHD_MAX_ACTIVE_REQUESTS`) and the adapter's own lane limit
(`WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS`, `WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS`,
`WHOOSHD_LLAMA_CPP_MAX_CONCURRENT_REQUESTS`) are independent. The broker's
`/runtime/capacity` reports the *broker's* effective limit; if the lane limit
is lower, requests beyond it are refused by the runtime even though the broker
reports headroom. **Read the lane limit, not just the broker limit, when
sizing concurrency.**

**Admission now respects the lane.** Each adapter may declare its own
concurrency ceiling. When a lane declares one, the broker's effective limit
for that request is capped by it, so a request that the lane cannot run is
**queued** rather than admitted to stall and be refused. The global limit is
not lowered for other lanes — a strict lane reserves capacity for itself
without constraining independent lanes.

Global capacity and lane capacity are **separate constraints over separate
counters**, and both are checked — at initial admission *and* when a queued
request is released. A saturated lane does not consume another lane's budget,
and an idle lane is not starved because a different lane is busy.

**A grant reserves capacity, not just ownership.** When the scheduler grants a
queued request — including a cache-affinity selection that hands the slot to a
*different* entry than the one being waited on — the execution slot is
reserved at the instant of the grant and held until the request actually
starts running. Without that reservation there is a scheduling gap between the
grant and the granted waiter resuming, and a request arriving in that gap
could take the slot that had already been handed out. The reservation is
taken against the **candidate's** lane as well as against global capacity, and
a candidate whose own lane is saturated is never granted.

Both `/v1/chat/completions` and `/v1/generate` resolve the route before
admitting, so the lane is known when the decision is made.

The MLX-VLM lane defaults to **one** concurrent request, which was chosen
without concurrency evidence. [CWC-013](../validation/2026-11-03-cwc013-lane-concurrency-experiment.md)
measured concurrency 2 on isolated brokers against the 12B: 2/2 success in
every run, zero swap-out in every warm band, and a verified clean lifecycle.
Both runs are **attested**: the harness sampled the broker's own admission
state during the x2 band and observed `peak_active_jobs = 2` with
`peak_queue_depth = 0`, so the two requests are demonstrably in flight
together.

Quote the gain as a **range**, not a figure. Across four attested runs the
aggregate throughput gain was **+66 %**, **+102 %**, **+69.5 %** and
**+91.0 %**, with a per-request latency cost between **−1.4 %** and
**+18.8 %**. The stable conclusion is that concurrency 2 *roughly doubles*
aggregate throughput on this host and checkpoint; the exact percentage is
not repeatable run to run.

**Concurrency 2 is not demonstrably free of paging cost.** Three of the four
runs recorded zero swap-out in every warm band; the fourth **failed its
thrash criterion** (30 016 pages swapped out in the x2 band) in the run that
followed the model being faulted back in from disk. The signal did not
reproduce in the run immediately afterwards, so the mechanism is an
association, not an established cause. If you raise the lane limit on a
working deployment, watch swap activity under representative load and be
ready to roll back.

Raising the lane limit on the working deployment is now an evidence-backed
choice rather than a guess — with that caveat attached.

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

### Per-model context policy

A `CapacityProfile` may carry token ceilings for the model it describes:

| Budget | Meaning | Effect when exceeded |
|---|---|---|
| hard max | Capability ceiling — never exceeded | Request rejected with `context_overflow` (HTTP 422) |
| recommended max | Default operating target | Request admitted unchanged; `context_reason` records the advisory |
| interactive max | Latency-sensitive path target | Request admitted; reason recorded |
| `max_input_tokens` | The model's maximum prompt size (text **and** media) | Request rejected with `context_overflow` (HTTP 422) |
| `max_output_tokens` | The model's maximum completion size | Request rejected with `context_overflow` (HTTP 422) |

Every request is costed in **assembled tokens**: input plus reserved output
plus multimodal content. A model with no calibrated budgets is never
constrained — the estimate is still reported as observability.

**Budgets follow the request's model.** The lookup is keyed by the *resolved*
model that will execute the request, not by the process-wide
`WHOOSHD_CAPACITY_MODEL_ID`. On a broker that serves several models this
matters: model A's hard ceiling is never applied to a request routed to model
B. A request whose model has no measured budgets is admitted unchanged.

**The counted prompt is the assembled prompt.** Tool definitions, assistant
`tool_calls`, structured-output schemas, tool choice, and stop sequences are
all forwarded upstream, so all of them are charged. The output reservation
follows the larger of `max_tokens` and `max_completion_tokens`, because the
broker forwards both and the upstream's precedence between them is not
knowable from here.

**Precision is reported, never assumed.** Every evaluation carries
`context_cost_status` — `exact`, `estimated`, or `unknown`. A request whose
media has no per-unit estimate is *undercounted*, not free, and its
hard-limit comparison is explicitly best-effort rather than a guarantee.

The model's maximum context window is a **capability ceiling, not a target
request size**. Routine interactive turns should sit near the recommended
budget; the hard maximum is for the rare request that genuinely needs it.

### Real-runtime calibration

`whooshd.bench.runtime_calibration` measures a live deployment: time to first
token, upstream-reported prefill cost, throughput, queue latency, host swap
activity, prompt-cache effectiveness, and lifecycle leak at rest.

```bash
scripts/calibrate_runtime.sh
```

It reads only, and writes a report; it never changes runtime state. Run it
through the launchd job when the measurement must happen outside an agent
sandbox — see [Runtime Calibration](ops/whooshd-runtime-calibration.md).

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

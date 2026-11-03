# Whoosh'd API Reference

This is the compact contract reference for the local provider surface that
Codexify consumes.

## Inventory Endpoints

- `GET /v1/models`
- `GET /api/tags`

Both endpoints must advertise the exact configured model id.

- Stub mode advertises `stub-model`
- MLX mode advertises `WHOOSHD_MLX_MODEL` verbatim

That contract lets Codexify validate `LOCAL_CHAT_MODEL` without relaxing its
provider gate or guessing at a stale alias.

## Runtime and Inspection Endpoints
- `GET /health`
- `GET /health/runtime`
- `GET /ready`
- `GET /runtime`
- `GET /runtime/model`
- `GET /runtime/requests`
- `POST /runtime/requests/{request_id}/cancel`
- `POST /runtime/model/warmup`
- `POST /runtime/model/unload`
- `GET /runtime/admission`
- `GET /runtime/capacity`

All runtime and inspection surfaces are metadata-only: they expose limits,
counters, lifecycle states, and status — never prompts, messages, generated
text, token IDs, KV handles, or credentials.

## Generation Endpoints

- `POST /v1/chat/completions`
- `POST /v1/generate`

## Control-plane contract

Clients that opt into the bounded machine-readable contract send:

```http
X-Whooshd-Contract-Version: whooshd.control.v1
```

Whoosh'd-owned responses advertise the same version, including non-2xx
responses. A missing request header keeps the legacy-compatible path; an
explicit unsupported version returns `contract_version_unsupported` with HTTP
400. Canonical error bodies carry a stable `code`, `http_status`, `retryable`,
optional bounded `retry_after_seconds`, request identity when available,
`category`, and bounded operational `details`.

See [Control-Plane Contract v1](control-plane-v1.md) for the full error matrix
and negotiation table.

## Request identity and correlation

Whoosh'd maintains two distinct request identities:

| Header | Owner | Notes |
|---|---|---|
| `X-Request-ID` | Upstream caller | Accepted only when 1–128 chars from `A-Za-z0-9._:-`; echoed on Whoosh'd-owned responses |
| `X-Whoosh-Request-ID` | Whoosh'd | Generated with a `whoosh-` prefix once a chat request enters the local lifecycle |

The upstream value never replaces the local lifecycle, queue, cancellation,
batch, or adapter identifier. Both survive queueing, batching, cancellation,
and adapter context. Unsafe or oversized incoming identifiers are omitted
rather than reflected.

A streaming failure that occurs after visible output has begun emits a
canonical SSE error event and then closes the stream **without** a successful
`[DONE]` sentinel. Clients must not treat stream EOF without a successful stop
chunk as a completed response.

## Route-specific capacity reservation

Each inference lane may declare its own concurrency ceiling. The broker's
global limit and a lane's ceiling are independent — a strict lane reserves
capacity for itself without lowering the limit for other lanes.

When a lane declares a ceiling, the broker's effective limit for a request
bound for that lane is capped by it. A request that the lane cannot run is
**queued** rather than admitted to stall at the lane's semaphore and be
refused.

The route is resolved **before** admission, so the lane is known when the
decision is made. Admission details report the lane decision:

```json
{
  "lane": "mlx_vlm",
  "lane_max_concurrent": 1,
  "lane_active_jobs": 0,
  "lane_limited": false
}
```

When a lane is saturated and the queue cannot take the request, the refusal
names the real constraint rather than the global limit:

```json
{
  "code": "runner_overloaded",
  "message": "The selected inference lane is at its concurrency limit (1).",
  "http_status": 429
}
```

## Per-model context policy

A `CapacityProfile` may carry three token ceilings for the model it
describes. Every request is costed in **assembled tokens** — input plus
reserved output plus multimodal content.

| Budget | Effect when exceeded |
|---|---|
| hard max | Rejected with `context_overflow` (HTTP 422) |
| recommended max | Admitted unchanged; `context_reason` records the advisory |
| interactive max | Admitted; `context_reason` records the tier |

A model with no calibrated budgets is **never silently constrained** — the
estimate is reported as observability and the request is admitted unchanged.

Admission responses carry the comparison:

```json
{
  "estimated_tokens": 412,
  "context_reason": "within_recommended",
  "context_budget": "recommended"
}
```

The rejection body names the budget that was breached:

```json
{
  "code": "context_overflow",
  "http_status": 422,
  "retryable": false,
  "category": "capacity_execution",
  "details": {
    "estimated_tokens": 41200,
    "hard_max_tokens": 32768,
    "context_breached_budget": "hard"
  }
}
```

The model's maximum context window is a **capability ceiling, not a target
request size**.

## Runtime provenance
Schema `whooshd.runtime.v1` attaches bounded execution evidence:

- `/v1/models` exposes it in each model's bounded metadata
- the native inventory exposes it as `runtime_provenance`
- non-streaming chat and generate responses carry an additive provenance field
- streaming chat keeps the established SSE body and sends provenance in
  `X-Whooshd-Runtime-Provenance`

Provenance records code-path evidence only. It is not proof that a live
runtime, model, or external collector is currently available.

## Request boundary

The permissive ingress request is filtered into an explicit backend request
after routing. Internal `metadata`, `threadwake`, reserved orchestration
namespaces, and undeclared extras do not reach adapters. See the
[request and backend boundary](request-contract.md) for the field matrix and
adapter extension policy.

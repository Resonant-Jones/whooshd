# Whoosh'd Queue Policy

Queue and admission control design specification.
**No implementation yet — this is a policy document.**

---

## Purpose

Define how future request queueing should work in Whoosh'd without changing
current runtime behavior.  This spec exists so that when queue implementation
begins, it has a clear contract rather than evolving through patch accretion.

---

## Non-Goals

This spec does **not** define:

- Multi-GPU scheduling
- Distributed inference
- Batching / continuous batching
- Prompt prefix caching
- ThreadWake persistent KV cache
- Embeddings endpoints
- Tool calling
- A Codexify-side API change

---

## Current Behavior (Phase 3A/3B)

Requests are either accepted for immediate execution or rejected.

```text
request arrives
  ↓
validate Pydantic contract
  ↓
evaluate admission (active limit, message count, prompt size, max_tokens)
  ↓
if rejected → structured error (429 / 400)
if accepted → begin_request → adapter.run → complete/fail/cancel
```

- `WHOOSHD_MAX_ACTIVE_REQUESTS` (default 2) bounds active execution.
- Overloaded requests return structured 429 before adapter invocation.
- Rejected requests do not create lifecycle records.
- `active_jobs` is computed from non-terminal requests.
- Streaming cancellation is cooperative (token-based).
- Non-streaming cancellation is best-effort.

---

## Future Queued Behavior

When queueing is enabled:

```text
request arrives
  ↓
validate
  ↓
admit or reject (structural/size/token limits still apply)
  ↓
if active_jobs < max_active_requests → run immediately
elif queue enabled and queue_depth < max_queue_depth → enqueue
else → return structured 429 (overloaded / queue full)
```

---

## Queue States

Proposed future lifecycle integration:

| State | Meaning |
|---|---|
| `accepted` | Request passed validation and admission |
| `queued` | Request is waiting in the queue for capacity |
| `running` | Adapter is executing (non-streaming) |
| `streaming` | Adapter is executing (streaming) |
| `completed` | Execution finished successfully |
| `cancelled` | Cancellation was requested and request terminated |
| `failed` | Execution raised an exception |
| `timed_out` | Queue wait time exceeded limit without execution |
| `expired` | Queue entry was removed for policy reasons (shutdown, etc.) |

The existing `accepted → running/streaming → completed/cancelled/failed`
lifecycle would gain an intermediate `queued` state before `running/streaming`.

Do not change existing enums until implementation.  This is design only.

---

## Admission vs Queueing

### Reject Before Queue

Reject immediately (no queue) when:

- Request is structurally invalid (422)
- Message count exceeds `WHOOSHD_MAX_MESSAGES`
- Prompt character estimate exceeds `WHOOSHD_MAX_PROMPT_CHARS`
- `max_tokens` exceeds `WHOOSHD_MAX_REQUEST_MAX_TOKENS`
- Model lifecycle is `failed` or `degraded` and cannot serve
- Queue is disabled AND active limit reached
- Queue is enabled AND queue is full AND active limit reached

### Queue When

Queue when:

- Request is valid
- Model/runtime can eventually serve (ready or warming)
- Queue is enabled (`WHOOSHD_ENABLE_QUEUE=true`)
- `queue_depth < WHOOSHD_MAX_QUEUE_DEPTH`
- `active_jobs >= WHOOSHD_MAX_ACTIVE_REQUESTS` (otherwise run immediately)

### Run Immediately When

Run immediately when:

- Request is valid
- Model/runtime can serve
- `active_jobs < WHOOSHD_MAX_ACTIVE_REQUESTS`

---

## Queue Policy Options

### Option A: Reject-Only (Current)

```
active_jobs >= max → 429
```

**Pros:** Simple, predictable, low memory risk.  
**Cons:** Caller must retry. Bursts lose work.

### Option B: Small Bounded FIFO Queue (Recommended First Queue)

```
if active_jobs < max → run
elif queue_depth < max_queue → enqueue
else → 429
```

**Pros:** Absorbs small bursts. Useful for Codexify agent/coding bursts.  
**Cons:** Introduces wait states. Requires cancellation-before-run. Requires queue timeout.

### Option C: Priority Lanes (Future)

```
interactive > agent > background
```

**Pros:** Protects UI responsiveness.  
**Cons:** Requires Codexify to send priority metadata. Risk of starvation. Not MVP.

**Manager recommendation for implementation:**

> Start with Option B (bounded FIFO queue) after throughput measurement.
> Priority lanes are explicitly parked until Codexify can provide priority metadata.

---

## Proposed Future Configuration

| Variable | Proposed Default | Purpose |
|---|---|---|
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable request queueing |
| `WHOOSHD_MAX_QUEUE_DEPTH` | `8` | Maximum queued requests |
| `WHOOSHD_QUEUE_TIMEOUT_SECONDS` | `120` | Max wait time before expiry |
| `WHOOSHD_QUEUE_POLL_INTERVAL_MS` | `25` | How often to check for capacity |

For current behavior, `WHOOSHD_ENABLE_QUEUE=false` matches Phase 3A/3B exactly.

---

## Cancellation Semantics

### Cancellation While Queued

```
queued request receives cancellation
  ↓
mark cancelled
  ↓
remove from queue (or skip on dequeue)
  ↓
never call adapter
  ↓
active_jobs remains unchanged
```

### Cancellation While Running / Streaming

Use existing Phase 3B behavior:

```
signal token → adapter cooperates → cleanup active_jobs
```

Streaming cancellation is cooperative. Non-streaming is best-effort.

### Client Disconnect While Queued

If the HTTP connection is waiting for queued execution and disconnects:

```
mark cancelled/abandoned
remove or skip queue entry
do not call adapter
```

For streaming endpoints, no SSE chunks should be emitted while queued.
Either hold the connection until execution begins, or return a `queued` status
depending on API mode.

**Manager recommendation:**

> Future queued streaming requests should not emit SSE chunks while queued.
> They should hold the connection or return a queued status.
> For OpenAI-compatible behavior, holding until execution begins is simplest,
> but queue timeout must be enforced and the connection closed on expiry.

---

## Timeout Semantics

Future timeout types:

| Timeout | Applies To | Result |
|---|---|---|
| Queue timeout | Waiting in queue before adapter call | `timed_out` / `expired`, no adapter call |
| First-token timeout | Streaming after adapter start | `failed` / `timed_out` |
| Generation timeout | Long-running generation | `failed` / `timed_out`, cooperative cancel |

**For immediate next implementation, only queue timeout is relevant.**

---

## Observability Requirements

Future queue implementation must expose (safe snapshots only, no prompts/messages):

| Metric | Description |
|---|---|
| `queue_enabled` | Whether queueing is active |
| `queue_depth` | Current number of queued requests |
| `max_queue_depth` | Configured queue limit |
| `oldest_queued_age_ms` | Age of oldest queued request |
| `running_request_count` | Requests currently executing |
| `total_queued` | Counter: requests ever enqueued |
| `total_dequeued` | Counter: requests removed from queue for execution |
| `total_queue_rejected` | Counter: queue-full rejections |
| `total_queue_timeout` | Counter: queue timeout expirations |
| `total_queue_cancelled` | Counter: requests cancelled while queued |

All snapshots must remain prompt/message/content-free.
No prompts. No messages. No generated text.

---

## Codexify Implications

### Current Behavior

```
429 = Whoosh'd is alive but at capacity.
Codexify may retry, back off, or mark provider degraded.
```

### Future Queue Behavior

**If the HTTP call waits (recommended for OpenAI compatibility):**

Codexify sees normal provider latency increase during queued wait.

**If a separate queued status is returned:**

Codexify would need explicit support for async provider jobs.
This is not recommended for MVP.

**Manager recommendation:**

> Preserve OpenAI-compatible synchronous HTTP behavior for
> `/v1/chat/completions`.  If queueing is added, it should happen
> internally and either begin execution before response streaming
> starts or return normal timeout/error responses.
> Do not force Codexify to adopt a new async provider protocol.

Codexify already has its own orchestration queue.  Whoosh'd should
not duplicate Codexify's role unless strictly necessary.

---

## Implementation Phases

| Phase | Scope | Gate |
|---|---|---|
| Phase 4A | Throughput measurement harness | Before any queue implementation |
| Phase 4B | Optional bounded FIFO queue | After measurement justifies it |
| Phase 4C | Priority lane hints | After Codexify can provide priority metadata |

---

## Open Questions

These should be resolved before queue implementation begins:

1. Should queued requests be visible in `/runtime/requests` with status `queued`?
   *Tentative answer: yes, with safe metadata only.*

2. Should queued requests count toward active admission limits?
   *Tentative answer: no — only `active_jobs` (running/streaming) counts.*

3. Should warmup trigger dequeue?
   *Tentative answer: yes — if model transitions to ready, eligible queued requests should be considered.*

4. Should queue be FIFO or LIFO?
   *Tentative answer: FIFO, with optional priority lanes later.*

5. Should streaming requests be queued differently from non-streaming?
   *Tentative answer: same queue, but streaming requests should not emit partial SSE while waiting.*

6. How to handle admission limits changing at runtime?
   *Tentative answer: dequeue check re-evaluates admission limits at dequeue time, not enqueue time.*

---

## Summary

```text
Current: reject-only (Phase 3A/3B)
  ↓
Future: optional bounded FIFO queue (Phase 4B)
  ↓
Later: priority lanes (Phase 4C)
```

- Queue is optional and disabled by default.
- Queue is bounded (default max depth 8).
- Queue respects cancellation before execution.
- Queue has configurable timeout.
- Queue snapshots are prompt-safe.
- Queue preserves OpenAI-compatible synchronous chat behavior.
- Priority lanes are explicitly parked.

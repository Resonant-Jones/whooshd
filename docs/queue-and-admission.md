# Queue and Admission

The little traffic goblin directing requests with a clipboard and
suspicious confidence.

## Purpose

Admission decides whether a request enters execution or queue.
The queue holds requests until capacity is available.
Together they prevent overload, preserve FIFO ordering, and enable
guarded adapter-batch grouping.

## Current Status

Implemented. FIFO queue with bounded depth, timeout, cancellation,
admission control, and HTTP grouping validation. Not production-
ready continuous batching.

## Admission Responsibilities

- Check active job count against `WHOOSHD_MAX_ACTIVE_REQUESTS`
- Reject structurally invalid requests (message count, prompt size, max_tokens)
- Offer queuing when queue is enabled and has capacity
- Return structured 429 with metadata on overload
- Track rejection counters per reason

## Queue Responsibilities

- Hold requests in FIFO order when capacity is full
- Wake waiters when an active slot opens
- Timeout requests that wait too long
- Support cancellation before execution
- Never emit SSE chunks while queued
- Report queue depth, oldest age, counters — metadata only

## Active Job Tracking

`active_jobs` counts requests in ACCEPTED, RUNNING, or STREAMING states.
Queued requests are excluded. Counters are exposed via `/runtime/admission`
and `/health`.

## FIFO Behavior

By default, the oldest queued request runs first. The scheduler
preserves FIFO ordering by default.

## Queue Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable bounded FIFO queue |
| `WHOOSHD_MAX_QUEUE_DEPTH` | `8` | Max queued requests |
| `WHOOSHD_QUEUE_TIMEOUT_SECONDS` | `120` | Max queue wait time |
| `WHOOSHD_QUEUE_POLL_INTERVAL_MS` | `25` | Capacity poll interval |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` | Max concurrent active requests |

## Request Lifecycle

```
request → admission → accepted → queue → scheduler → runtime → response
                  → rejected (429/400)
                  → timed out
                  → cancelled
```

## Scheduler Handoff

When capacity opens, the queue notifies the scheduler.
The scheduler selects the next request (FIFO default or cache-aware).

## Guarded Adapter-Batch Relationship

The queue groups compatible requests for guarded adapter batching.
HTTP grouping validation confirms two compatible requests can enter
the queue/admission path and form a group under test conditions.

See `docs/guarded-adapter-batch-http-grouping-validation.md`.

## HTTP Grouping Validation

PR #52 validated that two compatible requests can enter the HTTP
queue/admission path, queue behind a blocker, and complete
successfully under explicit guarded adapter-batch test conditions.

## Failure and Timeout Boundaries

- Queue timeout returns structured 429 with code TIMEOUT
- Cancellation removes from queue without calling adapter
- Queue full returns structured 429 with RUNNER_OVERLOADED
- All error responses are metadata-only

## Observability and Privacy

All queue/admission surfaces are metadata-only:
- Counters, depths, ages, limits
- No raw prompts, messages, generated text, token IDs, KV handles

See `/runtime/admission`, `/health`, `/runtime/requests`.

## Validation Coverage

- Unit tests: `tests/test_queue.py` (54 tests)
- Live smoke: `scripts/smoke_queue_live.sh`
- HTTP grouping: `tests/test_guarded_adapter_batch_http_grouping_validation.py`
- Docs boundary: `tests/test_guarded_adapter_batch_operator_docs.py`

## Non-Goals

- Production-ready continuous batching
- Latency/throughput improvement claims
- Token-step shared decode scheduling
- Priority lanes

## Related

- [Architecture](architecture.md)
- [Subsystems](subsystems.md)
- [validation-index.md](validation-index.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)

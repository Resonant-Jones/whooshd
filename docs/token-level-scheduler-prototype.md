# Token-Level Scheduler Prototype

Toy control tower. Fake-runtime only. No dragons taxiing.

## Purpose

Exercises the continuous batching runtime contract under fake movement:
admits requests into fake slots, advances prefill/decode ticks, demuxes
fake output chunks, handles cancellation/timeout/failure isolation, and
validates all invariants.

## Status

**Fake-runtime only.** No model inference. No backend wiring. No live
path changes.

## Lifecycle

```
ADMIT → PREFILL_PENDING → PREFILL_RUNNING → DECODE_ACTIVE → STREAM_DRAINING → COMPLETED
                                                                ↓
                                              CANCELLED / TIMED_OUT / FAILED (any point)
```

## Slots

```
EMPTY → RESERVED → PREFILL → DECODING → DRAINING → RELEASED
```

## What this proves

- Requests can be admitted into slots without duplicates
- Prefill and decode ticks advance lifecycle correctly
- Output chunks demux to correct request buffers
- Cancellation, timeout, and failure isolate peers
- Invariant validators catch slot, demux, and terminal-state violations
- Snapshots remain metadata-only

## What this does NOT prove

- Real backend batching behavior
- Real token generation
- Live-path integration
- Streaming demux at scale
- Production readiness

## Next Steps

- Fake streaming demux prototype
- Backend protocol draft
- Real backend prototype behind gates

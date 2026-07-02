# Continuous Batching Runtime Contract

Airport blueprint. No dragons taxiing. 🛫

## Status

**Contract-only.** Defines the runtime states, slot lifecycle, decode-step
contract, output demux rules, cancellation/timeout semantics, failure
isolation, and accounting invariants for Whoosh'd-owned continuous batching.

No implementation. No backend wiring. No token-level scheduler.

## Request Lifecycle

```
ADMITTED → PREFILL_PENDING → PREFILL_RUNNING → DECODE_ACTIVE → STREAM_DRAINING
                                                                     ↓
(any state can transition to) → COMPLETED / FAILED / CANCELLED / TIMED_OUT
```

## Slot Lifecycle

```
EMPTY → RESERVED → PREFILL → DECODING → DRAINING → RELEASED
                                                 → FAILED
```

## Invariants

- One slot maps to at most one request ID
- Released slots must not retain request IDs
- Terminal requests must not re-enter decode
- Output chunks must map to known active request/request ID
- Chunks must preserve per-request sequence order
- Runtime snapshots must be metadata-only (no prompts, tokens, generated text)

## Cancellation

- Before prefill: request never enters backend decode
- During prefill: controlled failure or safe removal
- During decode: slot release or tombstone
- After terminal state: idempotent (no-op)

## Failure Isolation

- Per-request failure: other peers continue
- Whole-step failure: all affected requests resolved
- Backend fatal failure: entered at token level until shutdown/reset

## Privacy

Runtime snapshots contain only counts, enums, booleans, request/slot IDs,
model IDs, backend names, and timestamps. No prompts, rendered prompts,
token IDs, generated text, KV handles, or cache internals.

## Next Steps

1. Token-level scheduler prototype (fake runtime, no backend)
2. Backend adapter protocol draft
3. Live implementation behind gates

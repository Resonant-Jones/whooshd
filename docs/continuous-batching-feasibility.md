# Continuous Batching Feasibility

Probe-only. No continuous batching is implemented.

## Three Contracts

```
1. Explicit batch execution
   Whoosh'd sends N compatible requests in one adapter call,
   receives N mapped responses. MLX supports this experimentally.

2. Server-side continuous batching
   Backend server accepts independent requests and interleaves
   decode steps internally. llama.cpp supports this.

3. Whoosh'd-owned continuous batching
   Whoosh'd controls token-level admission, decode steps,
   request slots, cancellation, per-request streaming/output.
   Not implemented. Requires new runtime primitives.
```

## Backend Findings

### MLX

- Explicit batch: **supported** (batch_generate, proven experimentally)
- Server-side continuous: **unsupported** (no server mode)
- Whoosh'd-owned continuous: **unsupported** (requires token-level decode control)
- Requires: token-level scheduler, slot accounting, stream multiplexing,
  cancellation protocol, per-request RNG state, stop tracking, failure isolation

### llama.cpp

- Explicit batch: **unsupported** (no N→N adapter call contract)
- Server-side continuous: **observable** (/slots, /metrics, busy/decode metrics)
- Whoosh'd-owned continuous: **not applicable** (server manages its own slots)
- Benefit: Whoosh'd can send concurrent requests and observe server-side behavior
  through metrics without owning decode scheduling

## Required Primitives Before Whoosh'd-Owned Continuous Batching

1. Token-level scheduler
2. Slot accounting (active decode slots, busy/idle)
3. Stream demultiplexing (per-request output channels)
4. Per-request cancellation inside active decode group
5. Timeout semantics for requests already admitted to decode
6. Per-request sampling/RNG state
7. Per-request stop-sequence tracking
8. Failure isolation (one failed decode must not corrupt peers)
9. No-leak runtime snapshots throughout token-level lifecycle

## Recommendation

```
llama.cpp: observe server-side behavior through /slots and /metrics
MLX: keep explicit batch execution as the primary batch path
Whoosh'd continuous batching: deferred until runtime primitives above are defined
```

Continuous batching remains feasibility-only. No implementation, no performance claims.

# Backend Token-Loop Feasibility

Probe-only. No backend token-loop implementation.

## Findings

### MLX

- **Ownership:** Whoosh'd-owned (plausible)
- **Token-step surface:** `generate_step` available in MLX-LM
- **Stream-chunk surface:** `stream_generate` → `GenerationResponse` chunks
- **Missing primitives:** slot ownership, cancellation hooks, timeout hooks,
  per-request sampling state, failure isolation, cleanup hooks
- **Status:** `plausible` — has the building blocks, needs a backend prototype
  to prove slot/cancellation/timeout/cleanup semantics
- **Live path:** unchanged, adapter unchanged

### llama.cpp

- **Ownership:** Backend server-owned
- **Token-step surface:** not available to Whoosh'd
- **Whoosh'd-owned token loop:** false — server owns slots and decode
- **Observable:** via /slots and /metrics (busy/decode metrics)
- **Status:** `observable` — useful as a backend-owned continuous batching
  reference, not a Whoosh'd-owned token loop
- **Live path:** unchanged, adapter unchanged

## Required Primitives Before Backend Prototype

Before any real backend feeds Whoosh'd's continuous batching contract:

1. Slot ownership — Whoosh'd must control which request occupies which slot
2. Cancellation hook — cancel a request during active decode
3. Timeout hook — timeout semantics inside decode
4. Per-request sampling state — independent sampling per request
5. Failure isolation — one request failure must not corrupt peers
6. Cleanup hook — release backend resources on terminal

## Next Steps

- MLX token-loop prototype (fake-live boundary, experimental gates)
- Manual local probe with real MLX model
- llama.cpp server-owned observation for reference metrics

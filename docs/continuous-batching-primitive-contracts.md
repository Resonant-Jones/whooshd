# Continuous Batching Primitive Contracts

Six locked doors with labels. Contract-only — no backend verification.

## The Six Primitives

| Primitive | Risk | Status |
|---|---|---|
| Slot ownership | Blocking | Contract defined |
| Cancellation hooks | Blocking | Contract defined |
| Timeout hooks | Blocking | Contract defined |
| Per-request sampling state | Blocking | Contract defined |
| Failure isolation | Blocking | Contract defined |
| Cleanup hooks | Blocking | Contract defined |

## What each contract defines

- **Slot ownership:** one request → one slot, released slots clear ownership,
  idempotent release
- **Cancellation:** no output after cancel in decode/prefill/draining phases,
  peer isolation, terminal idempotent
- **Timeout:** no output after timeout, terminal state, slot release
- **Sampling state:** per-request isolation, stop tracking via signatures
- **Failure isolation:** explicit scope (per-request, whole-step, batch,
  backend-fatal), peers continue unless escalation declared
- **Cleanup:** idempotent, releases slots, safe after partial failure,
  safe during shutdown

## Aggregate Readiness

```
all_contracts_defined = true
all_backend_verified = false
production_ready = false
live_path_enabled = false
blocking: all six primitives
```

## Next Steps

- Backend primitive verification for MLX (prove which keys MLX actually owns)
- Live continuous batching prototype (only after backend verification)

# MLX Primitive Verification

MLX empties its pockets — one key at a time. 🗝️

## Result

| Primitive | Status | Backend Verified | Blocks Live CB |
|---|---|---|---|
| Slot ownership | Unsupported | No | Yes |
| Cancellation hook | Partial | No | Yes |
| Timeout hook | Partial | No | Yes |
| Sampling state | Surface available | No | Yes |
| Failure isolation | Unknown | No | Yes |
| Cleanup hook | Partial | No | Yes |

## Aggregate

```
all_backend_verified = false
production_ready = false
live_path_enabled = false
adapter_behavior_changed = false
blocking: all 6 primitives
```

## What surfaces exist

- `stream_generate`: callable boundary for cancellation/timeout/cleanup (Python generator stop/close)
- `generate`: accepts sampling parameters (temperature, top_p, max_tokens)
- `GenerationResponse.finish_reason`: terminal signal available

## What's still missing

- No explicit slot ownership protocol
- No backend-side cancellation hook inside decode
- No backend-side timeout hook
- Per-request sampling isolation in continuous decode loop unproven
- Failure isolation in shared decode group unproven
- No explicit cleanup hook for slot/cache lifecycle under continuous batching

## Next Steps

- Cancellation/cleanup probe (generator close + slot cleanup)
- Per-request sampling state isolation proof
- Live continuous batching prototype (only after critical primitives are backend-verified)

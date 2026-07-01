# Batching Execution Skeleton

Motors on the bench, not in the factory.

## Status

Execution skeleton only. Batch execution is disabled by default and
gated behind explicit config + backend capability. Only the stub adapter
supports experimental batch execution for testing.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| WHOOSHD_BATCH_EXECUTION_ENABLED | false | Enable batch execution |
| WHOOSHD_BATCH_EXECUTION_MIN_SIZE | 2 | Minimum batch size |
| WHOOSHD_BATCH_EXECUTION_MAX_SIZE | 4 | Maximum batch size |

## Backend Capability

Backends report batch execution capability:

- `unsupported` — no batch execution (all real backends)
- `experimental` — stub adapter only, when config is enabled

## Gating

Batch execution requires ALL gates to pass:

1. `WHOOSHD_BATCH_EXECUTION_ENABLED=true`
2. Backend reports `experimental` or higher
3. Batching analysis finds an eligible group
4. Group size >= min and <= max
5. All requests are non-streaming and text-only
6. No request is cancelled/timed out

## Lifecycle

Each request in a batch retains its own lifecycle: queued, dequeued,
running, completed, failed, cancelled, timed out. Counters are per-request,
not per-batch.

## Limitations

- Stub adapter only — no real MLX, llama.cpp, or VLM batch inference.
- No continuous batching.
- No dynamic padding or token-level scheduling.
- No performance claims.
- Execution wiring into the live request path is deferred.

## Next Steps

- Real MLX batch inference proof
- Live execution wiring in the queue path
- Continuous batching feasibility

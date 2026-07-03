# Guarded MLX Adapter Batching

Experimental, explicitly gated, smoke-harness validated. Not production-ready.

## Summary

Whoosh'd now includes an experimental guarded MLX adapter-batch path for
compatible text-only non-streaming requests. The path is explicitly gated,
disabled by default, and validated within smoke-harness scope. It is not
true token-step continuous batching, not production-ready, and does not
claim latency or throughput improvement.

## What Changed

- Added guarded MLX adapter-batch implementation
- Added runtime validation packet
- Added smoke harness for enabled-smoke grouping
- Initial runtime validation: inconclusive (grouping lane not configured)
- Updated validation: passed within smoke-harness scope (`group_formed=true`)

## Operator Impact

No operator action is required by default. Existing behavior remains
unchanged unless guarded adapter-batch flags are explicitly enabled.

## Enablement

```bash
export WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=true
```

Both flags required. See operator guide for full configuration.

## Validation Status

- Smoke harness: passed (`group_formed=true`)
- HTTP queue/admission grouping: not validated
- Production readiness: not claimed

## Known Limitations

- HTTP queue/admission grouping not validated
- Production queue behavior not covered
- True token-step shared decode scheduler not implemented
- No streaming, tools, VLM, or non-MLX support

## Rollback

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

## Not Included

Default enablement, production queue behavior, HTTP queue/admission validation,
token-step shared decode scheduling, public streaming demux, VLM/llama.cpp
batching, benchmark reporting, latency/throughput claims, production readiness.

## Future Work

HTTP queue/admission grouping validation, operator-facing server-path validation,
benchmark methodology, true token-step shared decode scheduler research,
backend decode-loop ownership proof, shared-loop sampling/cancellation/timeout
proof, backend resource cleanup proof.

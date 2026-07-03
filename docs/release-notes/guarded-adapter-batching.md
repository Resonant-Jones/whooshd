# Guarded MLX Adapter Batching

Experimental, explicitly gated, disabled by default. Smoke-harness and HTTP grouping
validated. Not production-ready.

## Summary

Whoosh'd now includes an experimental guarded MLX adapter-batch path for
compatible text-only non-streaming requests. The path is explicitly gated,
disabled by default, and validated within smoke-harness scope and HTTP
queue/admission grouping scope. It is not true token-step continuous
batching, not production-ready, and does not claim latency or throughput
improvement.

## What Changed

- Added guarded MLX adapter-batch implementation
- Smoke-harness validation passed (`group_formed=true`)
- HTTP queue/admission grouping validation passed under explicit test conditions

The HTTP validation used two compatible requests through the full HTTP
queue/admission path. Both completed successfully, response shape remained
OpenAI-compatible, no internal metadata leaked, the queue drained, and
`active_jobs` returned to 0.

## Operator Impact

No operator action is required by default. Existing behavior remains
unchanged unless guarded adapter-batch flags are explicitly enabled.

## Validation Status

- Smoke harness: passed (`group_formed=true`)
- HTTP queue/admission grouping: passed (explicit test conditions)
- Production readiness: not claimed

These validation results do not claim production readiness, latency
improvement, throughput improvement, true token-step continuous batching,
or shared decode-loop scheduling.

## Known Limitations

- Not production-ready
- No latency or throughput claim
- Not true token-step continuous batching
- No shared decode-loop scheduling
- No public streaming demux
- No VLM batching
- No llama.cpp Whoosh'd-owned batching

## Rollback

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

## Not Included

Default enablement, production queue behavior, token-step shared decode
scheduling, public streaming demux, VLM/llama.cpp batching, benchmark
reporting, latency/throughput claims, production readiness.

## Future Work

Benchmark methodology, true token-step shared decode scheduler research,
backend decode-loop ownership proof, shared-loop sampling/cancellation/
timeout proof, backend resource cleanup proof.

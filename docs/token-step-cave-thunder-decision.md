# MLX Token-Step Cave Thunder Decision

Records the finding that MLX true token-step shared decode scheduling
remains research-only under the current integration. Guarded adapter
batching remains the correct near-term path.

## Decision

Under the current MLX integration, Whoosh'd cannot own or safely coordinate
the core primitives required for true token-step shared decode scheduling.
MLX token-step scheduling remains research-only. Guarded MLX adapter
batching remains the correct near-term path.

## Evidence

The MLX decode-step ownership spike classified:

| Primitive | Status | Impact |
|---|---|---|
| Prefill/decode split | blocked | Blocks token-step scheduling |
| Per-sequence handle | blocked | Blocks demux and cleanup |
| Selective decode step | blocked | Blocks per-step control |
| Stream demux | blocked | Blocks chunk routing |
| Sampler state | partial | Per-call, not per-active-sequence |
| Cancellation | partial | Generator boundary only |
| Timeout | partial | Generator boundary only |
| Cleanup | partial | Generator close only |
| Terminal observation | supported | Insufficient alone |
| Metadata safety | supported | Required baseline |

Result: `whooshd_owned_decode_loop_possible=false`, `recommended_next_step=keep_research_only`.

## Meaning of Cave Thunder

Cave Thunder means the backend can make sound, but Whoosh'd does not
currently have reins. In operator terms: true token-step shared decode
scheduling is blocked for MLX under the current integration.

## What Remains Supported

Guarded MLX adapter batching remains the correct near-term path —
implemented, gated, smoke-harness validated, HTTP grouping validated,
documented, and bounded by clear claim restrictions.

Guarded adapter batching is still: not production-ready, not making
latency/throughput claims, not true token-step continuous batching.

## What Remains Research-Only

- True token-step shared decode scheduling for MLX
- Whoosh'd-owned MLX decode loop
- Per-sequence MLX handle management
- Selective MLX decode-step scheduling
- MLX stream demux

## Fake Backend Contracts

The fake backend scheduler and isolation contracts remain valuable
architecture proofs. They prove the desired scheduler shape and
isolation invariants in a sandbox. They should not be used as
evidence that MLX supports Whoosh'd-owned token-step scheduling.

## Reopen Criteria

Do not begin a guarded internal MLX token-step prototype until:
- Prefill/decode split exposed or wrapped
- Per-sequence handle exposed or wrapped
- Selective decode step available
- Per-active-sequence sampler state available
- Cleanup/release per sequence available
- Terminal observation per sequence available
- Metadata-safe observability preserved

## Claim Boundaries

| Claim | Allowed? | Notes |
|---|---|---|
| Guarded adapter batching implemented | Yes | Experimental, gated |
| HTTP grouping validated | Yes | Explicit test conditions |
| MLX token-step scheduling implemented | No | Blocked |
| Whoosh'd-owned MLX decode loop possible | No | `whooshd_owned=false` |
| Production-ready | No | Not claimed |
| Latency/throughput improvement | No | Not benchmarked |

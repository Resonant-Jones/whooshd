# Real Backend Batching Feasibility

Probe-only. No live path batching is enabled for real backends.

## MLX

MLX-LM exposes `batch_generate(model, tokenizer, prompts, ...)` which
maps one call to N generated texts. The API signature is compatible
with Whoosh'd's batch execution contract: one adapter call, N responses,
preserved order.

**Feasibility status:** `feasible` (API shape compatible, import available)

**What's verified by probe:**
- `batch_generate` is importable
- Function signature accepts model, tokenizer, and prompts
- Prompt cache handoff is supported via `prompt_caches` kwarg

**What's NOT verified (requires manual smoke):**
- Response count correctness with real model
- Response ordering
- Prompt rendering fidelity (shared renderer)
- Sampling compatibility
- Memory/runtime failure behavior
- Lifecycle mapping

**Live path:** `unsupported`. No experimental MLX batch execution is
enabled until a manual smoke proves the requirements above.

## llama.cpp

llama.cpp server supports server-side continuous batching (parallel slots,
concurrent request handling, observable via `/metrics` and `/slots`).
But it does NOT expose an explicit batch chat completion API that maps
one adapter call to N mapped chat responses.

**Feasibility status:** `unsupported` (no explicit batch contract)

**Server-side batching:** `server_side_batching_only = true`
- Multiple concurrent requests are handled internally
- Observable via slots/metrics
- Not directly controllable as a single batch request

**Live path:** `unsupported`.

## Why server-side batching != explicit batch

```text
Server-side batching: backend handles N concurrent connections internally.
Explicit batch: Whoosh'd sends one batch of N requests, gets N responses.
```

These are different contracts. Whoosh'd's batch execution skeleton
requires the explicit contract. llama.cpp's server-side batching is
a different capability that may complement explicit batching later,
but does not replace it.

## Next Steps

- Manual MLX batch smoke: load a real model, render prompts via shared
  renderer, call `batch_generate`, verify response count and ordering.
- If smoke passes: MLX can graduate from `unsupported` to `experimental`
  for live path batch execution (separate PR).
- llama.cpp: explore whether a batch endpoint can be exposed or whether
  Whoosh'd should observe server-side concurrency through metrics.

# Batching Feasibility Analysis

Permit office for batched inference — not a conveyor belt. 🏗️

## Status

**Analysis-only.** Batching feasibility analysis identifies which queued
requests could theoretically be batched together. It does not execute
batched requests and does not change scheduling behavior.

## What it does

- Inspects safe queued metadata (model, backend, stream mode, image
  presence, sampling class)
- Groups compatible candidates
- Reports eligibility
- Respects max group size and token budget limits

## What it does NOT do

- Execute batched inference
- Reorder requests
- Change scheduler behavior
- Call backend batch APIs
- Make performance claims
- Expose raw prompt, message, token, or cache internals

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_BATCH_ANALYSIS_ENABLED` | `false` | Enable batch feasibility analysis |
| `WHOOSHD_BATCH_MAX_GROUP_SIZE` | `4` | Max requests per batch group |
| `WHOOSHD_BATCH_MAX_TOTAL_TOKENS` | `8192` | Max combined tokens per group |

## Compatibility Rules

Two requests are batch-compatible only if:

- Same model
- Same backend/runtime kind
- Both non-streaming
- Both text-only (no images)
- Same sampling class
- Combined estimated tokens under max

Streaming, vision, cross-model, and cross-backend batches are not
eligible in this analysis-only phase.

## Privacy

Batch analysis uses only safe metadata: request ID, model, backend,
stream mode, image presence, sampling class, token estimates. No raw
prompts, messages, generated text, token IDs, image content, KV handles,
or opaque refs are ever inspected or exposed.

## Next Steps

After batching feasibility analysis is proven, the next step is:

- Backend batch execution proof (e.g., MLX batched generation)
- Scheduler integration for batch-aware dequeue decisions

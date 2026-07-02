# Continuous Batching Implementation Plan

Beast definition pass. No fake mustache graduation. 🐉📋

## Decision

**Ship Option A first: guarded MLX adapter-batch implementation.**

**Keep Option B as future runtime project: true token-step shared decode scheduler.**

## Two Beasts

### Option A: Guarded Adapter-Batch Implementation

Uses queueing, eligibility, adapter batch seam, virtual slots, tombstones,
controlled errors, cleanup, metadata-only reports. Practical, proven,
guarded. NOT true token-step continuous batching.

### Option B: True Token-Step Shared Decode Scheduler

Requires backend decode-loop ownership, token-step scheduling, shared-loop
sampling isolation, backend cancellation/timeout/failure isolation.
Deeper runtime architecture. Not ready.

## Why Option A Ships First

- Closest to existing proven code
- Preserves current safety boundaries
- Has hardened failure behavior
- Does not require backend decode-loop ownership
- Can be guarded, measured, and rolled back
- Provides practical batching value without pretending

## Implementation Contract for Option A

### Must preserve

- Both gates required (disabled by default)
- Mixed groups rejected before generation
- No fallback after generation begins
- Controlled errors on failure
- All requests resolved
- All virtual slots tombstoned
- Cleanup idempotent
- Response shape parity
- Metadata-only internal reports
- `production_ready=false`

### Must not claim

- True token-step continuous batching
- Performance improvement
- Production readiness
- Default enablement

## Graduation Criteria

1. Guarded prototype merged
2. Hardening merged
3. Implementation plan merged
4. Follow-up implementation PR preserves all gates
5. Manual MLX smoke passes
6. No metadata leaks
7. No fallback after generation begins

## Option B Prerequisites (Future)

Backend decode-loop ownership, real shared-loop sampling isolation,
backend cancellation/timeout, shared-loop failure isolation, backend
resource cleanup, token-step scheduler integration.

## Terminology

| Term | Definition |
|---|---|
| Adapter batch | N requests → adapter call → N responses |
| Server-owned continuous batching | Backend owns slots and decode |
| Whoosh'd-owned token-step CB | Whoosh'd owns scheduling and decode |
| Guarded adapter-batch | Gated, eligibility-checked, safe adapter batch |

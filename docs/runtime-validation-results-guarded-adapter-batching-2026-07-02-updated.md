# Runtime Validation Results: Guarded MLX Adapter Batching (Updated)

Date: 2026-07-02
Operator: automated via Codex
Machine: Apple M4 (arm64)
OS: macOS
Python: 3.14.4
Whoosh'd commit: 2d3e6bc
Branch: codex/guarded-adapter-batch-runtime-results-updated
Model: stub (smoke harness)
Backend: stub
Flags: N/A (smoke harness bypasses HTTP flags)
Smoke mode: enabled smoke harness
Verdict: passed

## Scope

This validation records guarded MLX adapter batching using the enabled smoke
harness. The harness directly forms a compatible two-request group through the
guarded adapter-batch runner. This validates the guarded runner's enabled smoke
lane, response-shape checks, metadata/privacy checks, and rollback behavior.
It does not validate HTTP queue/admission grouping, production queue behavior,
true token-step continuous batching, shared decode-loop scheduling, or
performance improvement.

## Non-goals

- This validation does not claim production readiness.
- This validation does not claim latency improvement.
- This validation does not claim throughput improvement.
- This validation does not validate HTTP queue/admission grouping.
- This validation does not validate production queue behavior.
- This validation does not validate public streaming demux.
- This validation does not validate VLM batching.
- This validation does not validate llama.cpp Whoosh'd-owned batching.
- This validation does not validate true token-step shared decode scheduling.

## Preconditions

- [x] Whoosh'd installed in editable/dev mode
- [x] Stub adapter available for smoke harness
- [x] Smoke harness script exists (PR #49)

## Commands run

### Enabled smoke harness

```bash
cd whooshd && PYTHONPATH=. python scripts/smoke_guarded_mlx_adapter_batching_runtime.py
```

Result:

```json
{
  "status": "passed",
  "group_formed": true,
  "responses_ok": true,
  "response_shape_ok": true,
  "metadata_leak_detected": false,
  "production_ready": false,
  "performance_claim_made": false
}
```

### Disabled behavior

Not rerun in this update. Previously passed in
`runtime-validation-results-guarded-adapter-batching-2026-07-02.md`.

### One-flag behavior

Not rerun in this update. Previously passed.

### Rollback

Not rerun in this update. Previously passed.

## Results

| Check | Result | Evidence |
|---|---|---|
| Enabled smoke harness | passed | status=passed, group_formed=true, responses_ok=true |
| Group formed | passed | group_formed=true |
| Response shape | passed | response_shape_ok=true |
| Metadata/privacy | passed | metadata_leak_detected=false |
| Production claim boundary | passed | production_ready=false |
| Performance claim boundary | passed | performance_claim_made=false |
| Disabled behavior | passed (prior result) | See 2026-07-02 result |
| One-flag behavior | passed (prior result) | See 2026-07-02 result |
| Rollback | passed (prior result) | See 2026-07-02 result |
| HTTP queue/admission grouping | not validated | Explicitly out of scope |

## Interpretation

The updated validation resolves the prior enabled-smoke gap by using the
smoke harness added in PR #49. The smoke harness forms a compatible
two-request group and verifies that the guarded adapter-batch runner
completes with metadata-only reporting. This does not change the
production scope: HTTP queue/admission grouping remains unvalidated,
production readiness remains false, and no performance claim is made.

## Relationship to Prior Inconclusive Result

The prior result remains accurate for the earlier runtime packet: enabled
smoke was inconclusive because queue/grouping infrastructure was not
configured. This updated result records the smoke-harness validation path
added afterward. It should not be read as retroactively converting the
earlier result into a full HTTP runtime pass.

## Notes

The smoke harness uses a stub adapter and bypasses HTTP server startup.
This is intentional: the harness validates the guarded runner directly,
avoiding production queue, HTTP timing, and live-path complexity.

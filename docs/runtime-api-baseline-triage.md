# Runtime/API Baseline Triage

Triage document, not a repair patch. Captures the current test
baseline after the queue, batching, token-step research,
documentation, and release-facing closure arcs.

## Scope

Covers failing or unstable runtime/API tests observed in the
full suite. Excludes unrelated pre-existing backend-gated failures.

## Commands Run

```bash
.venv/bin/python -m pytest -v
```

## Current Result

```
2071 passed, 45 failed, 2 warnings
```

## Failure Clusters

### 1. Streaming chat completions (11 failures)

Tests: `test_chat_completions_streaming.py` (all 11 tests)

Pattern: 503 Service Unavailable for streaming path. Stub adapter
not serving streaming responses.

Likely layer: `whooshd/app.py` readiness check or adapter routing.

Suspected cause: Readiness/lifecycle contract change preventing
stub adapter from reporting ready for streaming.

Repair risk: Low. Likely a readiness endpoint or adapter routing fix.

### 2. Codexify provider compatibility (12 failures)

Tests: `test_codexify_provider_compat.py`

Pattern: Non-streaming and streaming probe failures. Provider
compatibility checks returning false or empty responses.

Likely layer: `whooshd/compat/` probe server + stub adapter.

Suspected cause: Provider contract expectations may have drifted
from current stub adapter output.

Repair risk: Moderate. May need probe contract alignment.

### 3. Generate contract (1 failure)

Test: `test_generate_model_id_passthrough`

Pattern: `/v1/generate` endpoint passthrough behavior changed.

Likely layer: `whooshd/app.py` generate handler.

### 4. Integration docs smoke (1 failure)

Test: `test_smoke_probe_passes_against_stub`

Pattern: Smoke probe fails against stub adapter.

### 5. Model lifecycle (2 failures)

Tests: `test_model_lifecycle.py`

Pattern: Runtime model endpoint shape or lifecycle state mismatch.

### 6. Readiness (1 failure)

Test: `test_readiness.py::test_smoke_probe_succeeds_against_stub`

Pattern: Smoke probe readiness check fails.

### 7. Request lifecycle (2 failures)

Tests: `test_request_lifecycle.py`

Pattern: Request tracking or cancellation lifecycle state mismatch.

## Likely Root Causes

- Readiness/health contract drift between stub adapter and app handler
- Provider compatibility probe expectations may need alignment
- Streaming path guard or adapter resolution may block stub in current app code

## Repair Ladder

```
1. Readiness / health contract stabilization
2. Request lifecycle contract
3. Non-streaming chat completions
4. Streaming chat completions / SSE
5. Generate endpoint compatibility
6. Codexify provider compatibility
7. Integration docs smoke
```

Reasoning: Readiness and lifecycle failures can create false
negatives in higher-level API tests. Stabilize base contract first.

## Non-Goals

- Fixing runtime behavior in this PR
- Rewriting API contracts
- Changing readiness semantics
- Weakening or deleting tests
- Marking failures as xfail
- Claiming full-suite pass
- Claiming production readiness

## Claim Boundaries

| Claim | Status |
|---|---|
| Baseline triage exists | Allowed |
| Full suite currently passes | Not claimed |
| Failures are categorized | Allowed |
| Root causes confirmed | Only when proven |
| Repair ladder exists | Allowed |
| Production readiness | Not claimed |
| Runtime correctness | Not claimed by triage alone |
| Performance improvement | Not claimed |

A triage report is scoped evidence about the test baseline.
It is not a fix and does not imply runtime readiness.

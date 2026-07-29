# CWC-008 — Bounded request correlation v1

## Objective

Implement the first live Whoosh'd-side request-correlation slice defined by the July 29, 2026 Codexify handoff and the shared fixture corpus introduced in PR #92.

Preserve two distinct identities:

- `X-Request-ID`: upstream Codexify request identity
- `X-Whoosh-Request-ID`: Whoosh'd-owned runtime execution identity

The upstream identifier must never replace the local lifecycle, queue, cancellation, batch, or adapter identifier.

## Starting point

Base this work on `codex/w0-contract-fixture-corpus`.

Read first:

- `contracts/fixtures/v1/contract-fixtures.json`
- `contracts/fixtures/v1/README.md`
- `docs/control-plane-v1.md`
- reverted PR #87 as historical reference only

Do not replay PR #87 wholesale.

## Scope

1. Add a bounded identifier helper with:
   - allowed characters: `A-Za-z0-9._:-`
   - maximum length: 128
   - generated Whoosh'd IDs prefixed with `whoosh-`
   - unsafe incoming identifiers omitted or replaced without reflection
2. Store the upstream request ID separately from the Whoosh'd request ID in request lifecycle records.
3. Echo a valid upstream `X-Request-ID` on Whoosh'd-owned responses.
4. Emit `X-Whoosh-Request-ID` after a chat request has entered the Whoosh'd lifecycle.
5. Preserve the identifier pair through:
   - immediate non-streaming execution
   - immediate streaming execution
   - queued execution
   - cancellation
   - pre-stream structured failure
   - mid-stream error without `[DONE]`
6. Do not add Codexify task IDs, attempt IDs, trace spans, or user identity in this slice.

## Non-goals

- No endpoint renaming
- No error-taxonomy migration
- No provider selection changes
- No ThreadWake KV behavior changes
- No queue-policy changes
- No batching redesign
- No authentication changes
- No persistence of prompt or generated content

## Required invariants

- The two IDs are distinct.
- Queueing never substitutes one ID for the other.
- Cancellation by Whoosh'd request ID returns the original valid upstream request ID when known.
- Admission rejection before lifecycle creation may echo only the valid upstream request ID.
- A mid-stream failure preserves the pair in response headers and emits no `[DONE]`.
- Unsafe or oversized incoming IDs never appear in response bodies, headers, logs, or lifecycle snapshots.
- Legacy callers without `X-Request-ID` continue to work.

## Tests

Add focused tests for:

- valid and invalid identifier normalization
- lifecycle snapshot separation
- success response headers
- rejection before local ID creation
- queued request correlation
- cancellation correlation
- streaming failure without `[DONE]`
- absence of prompt/body leakage

Run at minimum:

```bash
pytest -q \
  tests/test_contract_fixture_corpus.py \
  tests/test_control_plane_contract.py \
  tests/test_request_lifecycle.py \
  tests/test_queue.py \
  tests/test_cancellation.py \
  tests/test_request_correlation.py
```

Then run the full dependency-light suite available in the environment.

## Completion report

Report:

- changed files
- exact tests run and results
- compatibility decisions
- any paths that still lack end-to-end correlation
- whether the branch is safe to open as a draft PR

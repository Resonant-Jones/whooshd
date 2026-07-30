# Whoosh'd Contract Fixture Corpus v1

This directory contains repository-neutral fixtures for the Codexify ⇄ Whoosh'd integration boundary.

The fixture corpus is a **target contract artifact**, not proof that every target field or endpoint is already implemented on `main`. It exists so Whoosh'd and Codexify can validate the same wire-level expectations without importing source code from one another.

## Scope

`contract-fixtures.json` defines shared examples for:

- contract-version negotiation;
- Codexify request correlation and distinct Whoosh'd runtime identity;
- executable model records;
- stable error meanings;
- successful stream termination;
- failed stream termination;
- cancellation;
- capability mismatch;
- ThreadWake text normalization.

## Invariants

1. A successful stream includes a terminal `finish_reason: stop` event and `[DONE]`.
2. A failed stream never emits `[DONE]` and is never persistable as a canonical assistant response.
3. The Codexify request ID and Whoosh'd runtime request ID remain distinct.
4. Model records expose aliases and immutable revisions, never raw filesystem paths.
5. Capability mismatch fails before execution begins.
6. ThreadWake normalization is deterministic across repositories.
7. Fixture content contains no prompts, generated private content, credentials, user identity, or machine-specific paths.

## Current implementation relationship

Whoosh'd accepts the fixture target request header
`X-Whoosh-Contract-Version: 1`, retains the legacy request form
`X-Whooshd-Contract-Version: whooshd.control.v1`, and continues to accept
callers that send neither header. Matching target and legacy headers may be
sent together; unsupported or conflicting values are rejected before
execution. Responses continue to advertise only the legacy control-plane
identifier `X-Whooshd-Contract-Version: whooshd.control.v1`.

Likewise, some target error names intentionally differ from current implementation names. Those mappings must be resolved in a separately reviewed contract change. This fixture commit does not silently rename live errors.

## Mirroring into Codexify

Codexify should vendor the JSON fixture unchanged, or regenerate an identical artifact from a neutral schema package. Each repository should run its own conformance tests against the shared examples.

Changes to request identity, terminal streaming, error meaning, readiness, model records, or ThreadWake normalization require an explicit fixture-version decision.

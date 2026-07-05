# Runtime Validation

The immune system. Tells scoped evidence from inconclusive smoke,
operator-safe proof from goblins in lab coats. 🧪🦺

## Purpose

Runtime validation is Whoosh'd's evidence layer for checking whether
a runtime, backend, adapter path, or operator-facing behavior satisfies
a specific scoped contract under recorded conditions.

Runtime validation produces scoped evidence. It does not automatically
imply production readiness, latency improvement, throughput improvement,
or broad backend capability.

## What Runtime Validation Is

- Scoped evidence
- Recorded runtime proof
- Operator-safety boundary
- Backend behavior check
- Adapter capability check
- Manual smoke record
- Automated test companion
- Claim-boundary tool

A validation packet records what was tested, under what conditions,
what passed, what failed, what was inconclusive, and what claims are
allowed or forbidden afterward.

## What Runtime Validation Is Not

- Not a production certification
- Not a benchmark by default
- Not a latency/throughput claim
- Not universal backend support
- Not proof of token-step scheduling
- Not proof of continuous batching
- Not proof of durable cache safety
- Not a replacement for automated tests

A smoke test passing does not automatically create a performance claim.

## Evidence Levels

| Level | What it proves | What it doesn't prove |
|---|---|---|
| Documentation | Contract exists | Runtime behavior |
| Unit-test | Code contract holds | Live backend behavior |
| Fake-backend | Scheduler shape in sandbox | MLX capability |
| Manual smoke | Local path responded | Production readiness |
| Runtime validation | Scoped contract under recorded conditions | Broad backend support |
| Benchmark | Measured performance | Production readiness (separate claim) |
| Inconclusive | Observations only | The scoped claim |
| Blocked | Primitive unavailable | That the feature can't work later |

Higher evidence levels may permit narrower claims, but no evidence
level should be inflated beyond the exact conditions tested.

## Validation Packet Structure

- Summary
- Environment (machine, OS, Python, commit)
- Backend and model
- Configuration flags
- Commands run
- Expected behavior
- Observed behavior
- Result: passed / failed / skipped / inconclusive / blocked
- Allowed claims
- Forbidden claims
- Known caveats
- Follow-up work

Validation packets must include enough context to make the result
reproducible or clearly scoped.

## Result Meanings

- **Passed**: scoped contract observed under recorded conditions
- **Failed**: scoped contract not satisfied
- **Skipped**: validation did not run — not evidence
- **Inconclusive**: useful observations but did not prove the claim
- **Blocked**: required primitive or condition unavailable

Inconclusive is not failure cosplay and not success cosplay.
It is a recorded boundary.

## Runtime Smoke Tests

Smoke tests are live checks against a runtime or adapter path. They
can show that a path responds under recorded conditions, but they do
not by themselves prove production readiness, latency improvement,
throughput improvement, or broad model compatibility.

Smoke is evidence, not a crown.

## Manual vs Automated Tests

Automated tests prove repo-level contracts in repeatable conditions.
Runtime validation proves live runtime behavior under recorded operator
conditions. Both are useful, but neither should be inflated into claims
they did not test.

## Backend Validation

Backend validation is backend-specific. A result for one backend does
not imply support for another. Backend capability must be validated
before documentation claims operator-facing support.

## Guarded Batching Validation

Guarded adapter batching has scoped validation through smoke-harness
and HTTP queue/admission grouping records. That validation supports
the claim that guarded adapter batching exists and was validated under
recorded conditions. It does not support claims of production readiness,
latency/throughput improvement, true continuous batching, or token-step
shared decode scheduling. See [guarded-batching.md](guarded-batching.md).

## Queue/Admission Grouping

HTTP queue/admission grouping validation records that compatible work
grouped under explicit guarded adapter-batch test conditions. This does
not imply production batching or performance improvement.
See [queue-and-admission.md](queue-and-admission.md).

## Cave Thunder

The Cave Thunder decision records that MLX token-step shared decode
scheduling remains research-only. Blocked decisions are valid engineering
outcomes when they preserve evidence boundaries and prevent false claims.
See [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md).

## Operator Claim Boundaries

| Claim | Status |
|---|---|
| Validation packet exists | Allowed |
| Backend passed scoped validation | Allowed, scoped |
| Manual smoke passed under recorded conditions | Allowed, scoped |
| Guarded batching has scoped validation | Allowed |
| HTTP grouping validation passed | Allowed, scoped |
| Validation implies production readiness | Not allowed |
| Validation implies latency improvement | Not claimed |
| Validation implies throughput improvement | Not claimed |
| Smoke passing proves benchmark improvement | Not allowed |
| Fake backend proof proves MLX capability | Not allowed |
| Cave Thunder records MLX token-step blocked | Allowed |
| Token-step scheduling implemented for MLX | Not allowed |

## Developer Guidance

- New features require validation docs before operator-facing claims
- New backend support must identify backend-specific evidence
- New performance claims require benchmark packets
- New validation packets must include allowed and forbidden claims
- Do not promote fake backend proof into real backend claims
- Do not promote smoke passes into product claims

## Observability and Privacy

Metadata-only. Forbidden: raw prompts, rendered prompts, generated text,
token IDs, slot/tombstone IDs, sampling signatures, KV handles, cache
refs, model/tokenizer reprs, tracebacks, raw exceptions, plaintext
user identifiers.

## Validation Lifecycle

```
capability proposed → contract identified → test path selected →
environment recorded → commands run → observations captured →
result classified → allowed/forbidden claims recorded →
docs updated → follow-up identified
```

A validation result can be superseded by later runtime, backend,
model, or adapter changes.

## Non-Goals

Changing validation behavior, adding probes, rewriting tests, certifying
production, publishing benchmarks, claiming latency/throughput, claiming
universal backend support, claiming continuous batching, claiming
token-step scheduling, claiming durable cache safety.

## Related Docs

- [validation-index.md](validation-index.md)
- [guarded-batching.md](guarded-batching.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)
- [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md)
- [queue-and-admission.md](queue-and-admission.md)
- [scheduler.md](scheduler.md)
- [threadwake-prefix-cache.md](threadwake-prefix-cache.md)
- [subsystems.md](subsystems.md)

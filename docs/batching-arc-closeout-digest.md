# Batching Arc Closeout Digest

What we built, what we proved, what we blocked, and what nobody
should accidentally claim later. 🐉🏮

## Adapter Batching: Built, Validated, Documented

Guarded MLX adapter batching is implemented, explicitly gated, disabled
by default, smoke-harness validated, HTTP queue/admission grouping
validated, and operator-documented.

- Implemented with explicit two-flag gating
- Smoke-harness validation: passed (`group_formed=true`)
- HTTP queue/admission grouping: passed under explicit test conditions
- Operator guide, release note, claim boundary table published
- Virtual slot lifecycle, tombstones, controlled errors, metadata-only reports
- Not production-ready, no latency/throughput claims, not true token-step CB

## Token-Step Shared Decode: Researched, Fake-Proven, MLX-Blocked

True token-step shared decode scheduling has been fully researched
and the decision recorded.

- Fake backend scheduler contract: sequence handles, prefill/decode,
  demux, cleanup — proven
- Fake isolation contracts: sampler isolation, cancel/timeout/failure
  isolation, shared-step classification, no demux bleed — proven
- MLX decode-step ownership spike: Cave Thunder — blocked
- Decision recorded: `whooshd_owned_decode_loop_possible=false`
- Reopen criteria documented (7 prerequisites)
- Research-only for MLX under current integration

## PR Trail

| PR | Description |
|---|---|
| #51 | Operator docs / release note packet |
| #52 | HTTP queue/admission grouping validation |
| #53 | Operator caveat update (HTTP grouping) |
| #54 | Token-step shared decode scheduler research |
| #55 | Fake backend token-step scheduler contract |
| #56 | Fake backend isolation contracts |
| #57 | MLX decode-step ownership spike |
| #58 | Cave Thunder decision packet |

## Claim Boundaries

| Claim | Status |
|---|---|
| Guarded adapter batching implemented | ✅ |
| HTTP grouping validated | ✅ |
| Operator documented | ✅ |
| Fake token-step contracts proven | ✅ (sandbox) |
| MLX token-step scheduling implemented | ❌ Blocked |
| Production-ready | ❌ Not claimed |
| Latency/throughput improvement | ❌ Not claimed |
| Token-step shared decode scheduling | ❌ Research-only |

## Related Docs

- `docs/guarded-adapter-batching-operator-guide.md`
- `docs/guarded-adapter-batch-http-grouping-validation.md`
- `docs/continuous-batching-implementation-plan.md`
- `docs/token-step-shared-decode-scheduler-research.md`
- `docs/fake-token-step-scheduler-contract.md`
- `docs/fake-token-step-isolation-contracts.md`
- `docs/mlx-decode-step-ownership-spike.md`
- `docs/token-step-cave-thunder-decision.md`

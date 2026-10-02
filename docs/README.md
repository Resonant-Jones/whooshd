# Whoosh'd Documentation

Local-first inference orchestration: queueing, scheduling, capacity control,
runtime validation, model routing, guarded batching, and operator-safe local
model serving.

## Start Here

- **Operators**: [Operator Guide](operator-guide.md)
- **Developers**: [Developer Guide](developer-guide.md)
- **Architecture**: [Architecture Overview](architecture.md)
- **API**: [API Reference](api-reference.md)

## Control Plane

- [Capacity Controller](capacity-controller.md) — how many requests may execute
  right now; fixed/adaptive modes, operator ceiling, model/host-bound profiles
- [Throughput Control Plane Report](reports/throughput-control-plane.md) —
  recorded real-MLX validation results and the capacity recommendation
- [Control-Plane Contract v1](control-plane-v1.md) — bounded machine-readable
  error contract, version negotiation, correlation, streaming terminal integrity
- [Request and Backend Boundary](request-contract.md) — ingress vs. execution
  contract, field policy, runtime provenance, qualification attestation
- [Logging Safety Contract](security/whooshd-logging-safety.md) — bounded
  diagnostics and what remains unproven

## Reference

- [Subsystems](subsystems.md)
- [Glossary](glossary.md)
- [Validation Index](validation-index.md)
- [Arc Index](arc-index.md)
- [Operator Guide](operator-guide.md)

## Subsystem Deep Dives

- [Queue and Admission](queue-and-admission.md)
- [Scheduler](scheduler.md)
- [Model Registry](model-registry.md)
- [Model Management](model-management.md)
- [Guarded Adapter Batching](guarded-adapter-batching-operator-guide.md)
- [ThreadWake Cache](threadwake/README.md)

## Validation

- [Validation Index](validation-index.md) — current pass/fail/blocked/pending
  status across every recorded packet
- [Manual Runtime Validation](manual-runtime-validation.md)
- [Benchmarking](benchmarking.md) and
  [Benchmark Profiles](benchmark-profiles.md)

## Closure Digests

Historical closure records for completed arcs. These are dated evidence, not
current status:

- [Release-facing closure](release-notes/whooshd-queue-batching-docs-closure.md)
- [Claim ledger](release-notes/whooshd-queue-batching-docs-claim-ledger.md)
- [Documentation pass closeout](documentation-pass-closeout-digest.md)
- [Batching arc closeout](batching-arc-closeout-digest.md)
- [Cave Thunder decision](token-step-cave-thunder-decision.md)

## Key Boundaries

Whoosh'd does not claim production-ready continuous batching, latency
improvement, or throughput improvement unless a specific validation packet
and benchmark packet say so.

Guarded adapter batching is experimental, explicitly gated, disabled by
default, and validated within scoped test conditions.

Token-step shared decode scheduling remains research-only for MLX under the
current integration.

ThreadWake provides observation, scoring, and policy infrastructure. Backends
report `unsupported` KV capability by default, so production KV reuse is not
enabled. See [ThreadWake](threadwake/README.md).

Whoosh'd-owned log records are bounded by the
[logging safety contract](security/whooshd-logging-safety.md); historical
files, platform logs, and external collectors are not claimed to be
retroactively scrubbed.

## Current State

| System | Status |
|---|---|
| OpenAI-compatible chat and streaming | Supported |
| Control-plane v1 error contract | Supported |
| Request correlation (bounded v1) | Supported |
| Runtime provenance | Supported |
| Qualification attestation | Supported, target-scoped |
| Authoritative model registry | Supported, operator-owned fail-closed allowlist |
| Capacity control plane | Supported; `fixed` default, bounded `adaptive` |
| Queue and admission | Implemented |
| Scheduler | Implemented (FIFO + cache-aware) |
| Guarded adapter batching | Built, validated, documented, experimental |
| Token-step shared decode | Researched, fake-proven, MLX-blocked |
| ThreadWake | Observe-mode, KV skeleton; production KV reuse not enabled |
| MLX / MLX-VLM / llama.cpp runtimes | Supported, host validation required |
| Gemma 4 12B capacity calibration | Pending — host and checkpoint present, run not performed |

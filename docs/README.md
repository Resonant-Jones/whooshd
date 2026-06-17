# Whoosh'd Documentation Codex

Local-first inference broker for Apple Silicon — documentation index and
navigation guide.

---

## Getting Started

- **[README](../README.md)** — Project overview, compatibility matrix, core concepts
- **[MLX Environment Setup](mlx-environment.md)** — Apple Silicon MLX backend installation and configuration
- **[Codexify for Whoosh'd](codexify-for-whooshd.md)** — How Codexify uses Whoosh'd as a local sidecar provider

## Operations & Configuration

- **[Model Management](model-management.md)** — Downloading, storing, and switching models
- **[Model Registry](model-registry.md)** — Registered model inventory, compatibility, and candidate inspection
- **[Queue Policy](queue-policy.md)** — Admission control, concurrency, and rejection behavior

## Codexify Integration

- **[Codexify Integration Guide](codexify-integration.md)** — Configuration, health vs readiness, streaming, cancellation
- **[Codexify Live Rehearsal Runbook](codexify-live-rehearsal.md)** — Step-by-step integration test procedure
- **[Codexify Drop-In Smoke Test](codexify-drop-in-smoke-test.md)** — Quick smoke test for OpenAI-compatible drop-in
- **[Codexify Managed Sidecar Provider](codexify-managed-sidecar-provider.md)** — Managed sidecar architecture
- **[Codexify Runtime Contract Review](codexify-runtime-contract-review.md)** — Contract alignment and compatibility review

## ThreadWake Cache

ThreadWake is a runtime optimization that reuses pre-computed prompt-prefix state
across chat requests.  See the [ThreadWake Overview](threadwake/overview.md) for
a conceptual introduction.

### Core Documentation

| Doc | Description |
|---|---|
| [Overview](threadwake/overview.md) | What ThreadWake is, what it isn't, how it works, modes, scope, lifecycle |
| [Configuration](threadwake/configuration.md) | All environment variables, request-level overrides, health/flush endpoints |
| [Security & Privacy](threadwake/security.md) | Scope enforcement, KV sensitivity, flush behavior, recommendations |
| [Metrics & Observability](threadwake/metrics.md) | Health endpoint fields, internal counters, interpretation guidance |

### Integration

| Doc | Description |
|---|---|
| [Codexify Integration](threadwake/codexify-integration.md) | Request contract for `threadwake_segments`, validation rules, segment type mapping, Codexify config, safe wording |

### Architecture & Research

| Doc | Description |
|---|---|
| [Backend Tokenizer Adapter Matrix](threadwake/backend-tokenizer-adapter-matrix.md) | Which backends can provide real tokenization; Phase M3 recommendation |
| [Durable Snapshots Research](threadwake/durable-snapshots-research.md) | Feasibility analysis for persistent KV snapshots; RECOMMEND DEFER |

## Benchmarking

- **[Benchmarking Guide](benchmarking.md)** — How to run throughput benchmarks against a running Whoosh'd server
- **[Benchmark Profiles](benchmark-profiles.md)** — Pre-configured benchmark scenarios
- **[Example: MLX Findings](examples/mlx-benchmark-findings.md)** — Real MLX benchmark results
- **[Example: Stub Report](examples/stub-benchmark-report.md)** — Stub adapter benchmark sample
- **[Template: Benchmark Report](templates/benchmark-report.md)** — Template for new benchmark reports

## Runtime Validation

- **[Manual Runtime Validation](manual-runtime-validation.md)** — Procedure for validating runtime behavior
- **[MLX Validation Results](runtime-validation-results-mlx-2026-06-13.md)** — MLX runtime validation report
- **[llama.cpp Validation Results](runtime-validation-results-llama-cpp-2026-06-13.md)** — llama.cpp runtime validation report
- **[MLX-VLM Validation Results](runtime-validation-results-mlx-vlm-2026-06-13.md)** — MLX-VLM runtime validation report
- **[MLX-VLM Template](runtime-validation-results-mlx-vlm-template.md)** — Template for new MLX-VLM validation reports
- **[Validation Template](runtime-validation-results-template.md)** — Generic runtime validation report template

## API Reference

- **[API Reference](api-reference.md)** — Endpoint reference for all Whoosh'd API surfaces

## Releases

- **[v0.1 Release Candidate](releases/v0.1-rc.md)** — v0.1 release notes
- **[Release Checklist](releases/release-checklist.md)** — Pre-release verification checklist
- **[v0.1rc1 Handoff](handoff/whooshd-v0.1rc1-handoff.md)** — Handoff bundle for v0.1rc1

## Architecture

### ThreadWake Module Map

```
whooshd/runtime/threadwake/
├── __init__.py              Public API surface
├── types.py                 Data contracts (PromptGraph, ThreadWakeObservation, etc.)
├── compiler.py              Prompt segmentation and canonicalisation
├── keys.py                  Deterministic hashing and cache key construction
├── policy.py                Eligibility policy + snapshot policy engine
├── manager.py               Central coordinator (observe, ephemeral, session)
├── index.py                 Scoped in-memory metadata index with LRU eviction
├── metrics.py               Bounded-cardinality metrics and counters
├── backend.py               KV-capable backend interface + FakeKVBackend
├── handles.py               KVHandle model and KVCapability enum
├── tokenization.py          Backend-owned tokenization interface
├── mlx_tokenizer.py         MLX in-process tokenizer adapter
├── metadata.py              Codexify segment metadata validation
├── kv_lifecycle.py          KV lifecycle observer and event ring-buffer
├── prefix_proof.py          Exact token-prefix comparison engine
├── candidate_selection.py   Candidate scoring and selection
├── replay_analysis.py       Replay-style analysis of candidate telemetry
├── storage.py               Optional SQLite candidate/storage persistence
├── snapshot_manifest.py     Manifest model + material contract + validator
├── snapshot_creation.py     Experimental snapshot creation gate
├── materialization.py       Backend materialization interface + registry
├── artifacts.py             Snapshot artifact registry
└── snapshot_material.py     Snapshot materialization contract
```

### Key Concepts

| Concept | Module | Description |
|---|---|---|
| Prompt Graph | `compiler.py` | Deterministic segmentation of chat messages into stable/dynamic layers |
| Cache Key | `keys.py` | SHA-256 based key from model, backend, tokenizer, template, prefix |
| Scope Enforcement | `index.py` | Thread/user/project/global scope boundaries for cache lookups |
| KV Capability | `backend.py` | Per-backend capability reporting (unsupported → serializable) |
| Tokenization | `tokenization.py` | Backend-owned token ID extraction (real or estimate-only) |
| Prefix Proof | `prefix_proof.py` | Exact token equality check between two prompts |
| Snapshot Policy | `policy.py` | Deterministic eligibility rules for snapshot candidates |
| Materialization | `materialization.py` | Backend-facing interface for snapshot materialization |

### Test Map

Tests are organized by phase. Run the full suite:

```bash
pytest tests/test_threadwake_*.py -q
```

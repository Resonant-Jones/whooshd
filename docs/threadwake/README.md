# ThreadWake Documentation

ThreadWake is a **runtime optimization** for Whoosh'd that reuses
pre-computed prompt-prefix state across chat requests.  It improves
latency for repeated long-context workflows.

---

## Documentation Map

### Getting Started

| Doc | Description |
|---|---|
| [Overview](overview.md) | What ThreadWake is, how it works, modes, scope, lifecycle |
| [Configuration](configuration.md) | Environment variables, request overrides, health/flush endpoints |

### Operating

| Doc | Description |
|---|---|
| [Operator Runbook](operator-runbook.md) | How to use observe mode, run analysis, read reports, interpret counts |

### Observability

| Doc | Description |
|---|---|
| [Metrics & Health](metrics.md) | Health endpoint fields, internal counters, interpretation |

### Security

| Doc | Description |
|---|---|
| [Security & Privacy](security.md) | Scope enforcement, KV sensitivity, flush behavior |

### Integration

| Doc | Description |
|---|---|
| [Codexify Integration](codexify-integration.md) | Request contract for `threadwake_segments`, validation, segment mapping |

### Architecture & Research

| Doc | Description |
|---|---|
| [Backend Tokenizer Adapter Matrix](backend-tokenizer-adapter-matrix.md) | Which backends can provide real tokenization |
| [Backend Snapshot Feasibility](backend-snapshot-feasibility.md) | M19 verdict: no backend is production-ready for KV snapshot materialization |
| [Durable Snapshots Research](durable-snapshots-research.md) | Feasibility analysis for persistent KV snapshots; RECOMMEND DEFER |

---

## Current Milestone Status

**ThreadWake Metadata Milestone — June 2026**

| Component | Status |
|---|---|
| Metadata spine (M14-M18) | ✅ Merged — artifact registry, creation gate, material contract, validation, materialization interface |
| Backend feasibility (M19) | ✅ Documented — no backend production-ready for KV materialization |
| Analysis loop (M20) | ✅ Available — metadata-only periodic analysis outside inference path |
| Visibility surfaces (M21) | ✅ Available — `GET /runtime/threadwake/analysis` + CLI |
| Operator runbook (M22) | ✅ Documented |
| Visibility docs polish (M23) | ✅ Documented |
| Real KV reuse | ❌ Not enabled |
| Durable KV snapshots | ❌ Deferred |
| Production KV materialization | ❌ No backend supports it |

---

## What ThreadWake Does Today

- **Observes** prompt prefixes and identifies which ones are stable vs dynamic
- **Measures** potential benefit via candidate telemetry (token counts, hit rates, reuse ratios)
- **Analyzes** which prefixes would be worth caching via the snapshot policy engine
- **Reports** analysis results via HTTP and CLI — counts only, no raw content
- **Materializes nothing** — all manifests and artifacts are metadata-only
- **Degrades safely** — all features off by default, no impact on inference when disabled

## What ThreadWake Explicitly Does Not Do Yet

- **Does not persist KV tensors** — no snapshot file, no backend cache serialization
- **Does not restore KV state** — no `load_kv_cache`, no `generate_from_kv` with real backends
- **Does not reuse KV across backends** — all production backends report `UNSUPPORTED` or `DECLARED`
- **Does not expose raw content** — no prompts, token IDs, user IDs, or scope IDs in any visibility surface
- **Does not require SQLite** — all persistence is optional and disabled by default
- **Does not change inference output** — ThreadWake is purely additive and opt-in

---

## Next Safe Phases

| Phase | Description |
|---|---|
| M25 | Docs/readiness review or release tag |
| M26 | Observe-mode UX polish |
| M27 | Fake/test backend lifecycle harness |
| Future | Real backend materialization — only if a backend exposes a stable, documented, identity-bound public KV snapshot API |

---

## Safety Boundary

ThreadWake treats every candidate as a hypothesis. It decides what *would* be
worth caching, then stops. No KV tensors are created, persisted, restored, or
reused. All visibility surfaces return counts and status only. The system is
designed to be safe even when fully enabled — the worst case is a cache miss.

The full safety posture is documented in:
- [M19 Backend Snapshot Feasibility](backend-snapshot-feasibility.md)
- [Security & Privacy](security.md)
- [Operator Runbook](operator-runbook.md)

# Changelog

## Optional Bounded Request Queue — Phase 4B (2026-06-26)

Feature-flagged bounded FIFO request queue behind `WHOOSHD_ENABLE_QUEUE`.

### Added

- Bounded FIFO request queue (disabled by default; `WHOOSHD_ENABLE_QUEUE=false`)
- Queue env config: `WHOOSHD_ENABLE_QUEUE`, `WHOOSHD_MAX_QUEUE_DEPTH` (8),
  `WHOOSHD_QUEUE_TIMEOUT_SECONDS` (120), `WHOOSHD_QUEUE_POLL_INTERVAL_MS` (25)
- `QUEUED` and `TIMED_OUT` request lifecycle states
- Queue observability via `GET /runtime/admission` (counters and depth)
- Cancel/timeout queued requests without invoking the adapter
- Streaming: no SSE chunks emitted while queued
- 50 queue tests covering disabled behavior, enqueue/dequeue, queue full,
  timeout, cancellation, streaming hold, and no-leakage guarantees

### Unchanged

- Default reject-only 429 behavior preserved
- OpenAI-compatible API contract unchanged
- No priority lanes, batching, prompt-prefix caching, ThreadWake KV reuse,
  embeddings, tool calling, or durable snapshots

## Runtime Readiness Smoke Layer (2026-06-19)

Tag: `whooshd-runtime-readiness-smoke-2026-06`

Turned the README quickstart into executable artifacts with live verification.

### Added

- Example env files: `examples/env.stub`, `examples/env.codexify`,
  `examples/env.mlx.example`, `examples/env.llama-cpp.example`
- Smoke scripts: `scripts/smoke_stub.sh`, `scripts/smoke_threadwake.sh`,
  `scripts/smoke_openai_compat.sh`
- Live stub smoke verification: 12/12 checks passing

### Docs

- README runtime surface map, local sanity checklist, Codexify connection notes,
  ThreadWake milestone status table, troubleshooting guide

---

## ThreadWake Metadata Milestone (2026-06-19)

Tag: `threadwake-metadata-milestone-2026-06`

Pre-materialization platform layer for prompt-prefix reuse optimization.

### Added

- **Metadata spine** (M14–M18): artifact registry, snapshot creation gate,
  material contract, material validation, backend materialization interface
- **Backend feasibility investigation** (M19): no backend is production-ready
  for real KV snapshot materialization
- **Metadata-only analysis loop** (M20): periodic analysis outside inference path
- **Read-only visibility surface** (M21): `GET /runtime/threadwake/analysis` +
  `python -m whooshd.threadwake.analyze`
- **Operator runbook** (M22): safe usage, field interpretation, scenarios,
  safety checklist
- **Visibility docs polish** (M23): "which surface?" table, operator workflow,
  example outputs
- **Documentation index** (M24): consolidated threadwake docs map, milestone
  status, safety boundary

### Safety

- KV materialization: **not enabled**
- Durable snapshots: **deferred**
- Production restore: **not implemented**
- Backend materialization: no backend supports it
- All visibility surfaces: counts and status only — no raw prompts, token
  IDs, opaque refs, or user identifiers

### Scope

- 24 source modules, 47 test files, 10 doc pages, 3 benchmark scripts
- 614 tests passing, 4 skipped

---

## v0.1.0rc2 (2026-05-29)

### Added

- Release hygiene tag capturing the configured-model inventory alignment and successful live Codexify integration proof.

## v0.1.0rc1 (2026-05-17)

### Added

- OpenAI-compatible `POST /v1/chat/completions` (non-streaming and SSE streaming)
- `GET /v1/models` (OpenAI format) and `GET /api/tags` (Ollama format)
- `GET /health` (liveness) and `GET /ready` (readiness, 200/503)
- `GET /runtime` and `GET /runtime/model` (lifecycle snapshots)
- `POST /runtime/model/warmup` and `POST /runtime/model/unload`
- `GET /runtime/requests` and `POST /runtime/requests/{id}/cancel`
- `GET /runtime/admission` (limits and counters)
- `POST /v1/generate` (Codexify-style generation)
- Optional `mlx-lm` backend with lazy loading and streaming via `stream_generate`
- Stub inference adapter (always available, no dependencies)
- Adapter factory with `WHOOSHD_ADAPTER=stub|mlx` selection
- Request lifecycle tracking with `active_jobs` computed from live state
- Cancellation signaling with per-request `CancellationToken`
- Adapter-aware cancellation (stub and MLX stream cooperation)
- Admission control with configurable limits (`429 RUNNER_OVERLOADED`)
- Throughput benchmark harness (`python -m whooshd.bench.runner`)
- Codexify provider smoke probe (`python -m whooshd.compat.probe_server`)
- SSE stream parser (`reconstruct_assistant_text`) for Codexify compatibility

### Verified

- 343 automated tests passing without `mlx-lm` installed
- Real MLX non-streaming smoke validated (Llama-3.2-3B-Instruct-4bit, 4-bit)
- Real MLX streaming smoke validated (token-by-token → `[DONE]`)
- Real MLX benchmarks: concurrency 1 (4/4, 370ms), concurrency 2 (8/8, 672ms)
- Admission control validated under real MLX load (concurrency 4: 7/12 rejected)
- `active_jobs` returns to 0 after all benchmark runs
- Live Codexify rehearsal completed end to end with `provider id=local`, `displayName=Whoosh'd`, and persisted assistant message `12412`
- `mlx-lm` remains optional via `pip install -e ".[mlx]"`

### Documentation

- Codexify integration guide
- Codexify runtime contract review
- Live integration rehearsal runbook
- Live Codexify integration proof recorded
- MLX environment setup guide
- Benchmark profiles and report template
- Queue policy design spec
- Real MLX benchmark findings packet
- Release candidate notes and checklist

### Known Limitations

- Live Codexify integration completed and documented
- Queue not implemented (not yet justified by burst evidence)
- MLX requires Apple Silicon and macOS 14+
- Non-streaming MLX cancellation is cooperative/best-effort
- No embeddings, tool calling, vision, batching, or ThreadWake

### Deferred

- Request queue (designed, not implemented)
- Priority lanes
- Batching / continuous batching
- ThreadWake persistent KV cache
- Prompt prefix caching

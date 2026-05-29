# Changelog

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

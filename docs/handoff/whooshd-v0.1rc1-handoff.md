# Whoosh'd v0.1rc1 Handoff

How to resume work on Whoosh'd without re-reading the entire history.

---

## Current Status

- **Version:** 0.1.0rc1
- **Test count:** 343 (all passing)
- **Runtime state:** Stable. Live Codexify integration verified; no known bugs in admission, cancellation, lifecycle, or adapter boundaries.
- **MLX state:** ✅ Verified. Real smoke and benchmarks complete for Llama-3.2-3B-Instruct-4bit (4-bit).
- **Codexify integration state:** ✅ Completed. Live rehearsal succeeded end to end.

---

## What Works

- OpenAI-compatible `POST /v1/chat/completions` (non-streaming + streaming SSE)
- `GET /v1/models`, `GET /api/tags`
- `GET /health` (liveness), `GET /ready` (readiness, 200/503)
- `GET /runtime`, `GET /runtime/model`, `GET /runtime/admission`, `GET /runtime/requests`
- `POST /runtime/model/warmup`, `POST /runtime/model/unload`
- `POST /runtime/requests/{id}/cancel`
- Stub backend (always available, no dependencies)
- Optional MLX backend (requires `mlx-lm`)
- Adapter factory (`WHOOSHD_ADAPTER=stub|mlx`)
- Request lifecycle tracking (`active_jobs` computed from live state)
- Cancellation signaling (per-request `CancellationToken`, adapter-aware)
- Admission control (configurable limits, `429 RUNNER_OVERLOADED`)
- Throughput benchmark harness (`python -m whooshd.bench.runner`)
- Codexify provider smoke probe (`python -m whooshd.compat.probe_server`)
- SSE stream parser (`reconstruct_assistant_text`)

---

## What Is Verified Manually

| Check | Result |
|---|---|
| MLX non-streaming smoke | ✅ `chat.completion`, finish=stop |
| MLX streaming smoke | ✅ Token-by-token → `[DONE]` |
| MLX concurrency 1 benchmark | ✅ 4/4, 370ms latency, 282ms TTFT |
| MLX concurrency 2 benchmark | ✅ 8/8, 672ms latency, 458ms TTFT |
| MLX overload benchmark | ✅ 12/16 rejected (429), 0 errors |
| `active_jobs` cleanup | ✅ Returns to 0 after all runs |
| Live Codexify chat turn | ✅ `task.progress`, `task.chunk`, `task.completed`, assistant message `12412` persisted |

---

## What Is Blocked

- **Live Codexify integration rehearsal** — none for the verified path. Runbook and proof: `docs/codexify-live-rehearsal.md`.

---

## What Is Intentionally Deferred

- Request queue
- Scheduler / adaptive concurrency
- Batching / continuous batching
- ThreadWake persistent KV cache
- Prompt prefix caching
- Embeddings endpoint
- Tool calling / function calling
- Vision / multimodal MLX
- Multi-model concurrent routing
- Production authentication hardening
- Priority lanes

---

## How To Run Stub

```bash
WHOOSHD_ADAPTER=stub python -m uvicorn whooshd.app:app --reload
```

No dependencies beyond the base install. Always available for testing.

---

## How To Run MLX

```bash
pip install -e ".[mlx]"
WHOOSHD_ADAPTER=mlx \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  python -m uvicorn whooshd.app:app --host 0.0.0.0 --port 8000

# Warm model
curl -X POST http://localhost:8000/runtime/model/warmup
curl -i http://localhost:8000/ready
```

Requires Apple Silicon, macOS 14+, and the optional `mlx` extra.

---

## How To Run Benchmarks

```bash
# Stub
python -m whooshd.bench.runner --base-url http://localhost:8000 --stream

# MLX (after warmup)
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 2 --requests 8 --stream --max-tokens 128 --json
```

---

## How To Resume Codexify Integration

When re-running against a fresh Codexify environment:

1. Start Whoosh'd with MLX (see above).
2. Warm model.
3. Configure Codexify local provider (see `docs/codexify-integration.md`).
4. Follow the rehearsal runbook in `docs/codexify-live-rehearsal.md`.
5. Record results and decide next action.

**Live integration status: completed. Re-run the runbook only if the environment changes.**

---

## Do Not Do Next

Do not start any of these before completing live Codexify integration:

- Queue implementation
- Scheduler / batching
- ThreadWake
- Prompt prefix caching
- Embeddings / tool calling / vision
- Multi-model routing
- Production auth hardening

The queue is designed. The benchmarks are measured. The admission control works.
Do not build a waiting room before proving there is a line.

---

## Parked Work

These are designed but not yet justified by evidence:

| Feature | Design Doc | Status |
|---|---|---|
| Queue | `docs/queue-policy.md` | Policy designed, not implemented |
| ThreadWake | `threadwake-spec.md` | Spec exists, parked |
| Priority lanes | `docs/queue-policy.md` | Future, not MVP |
| Batching | `whooshd_high_throughput_architecture.md` | Architecture spec exists |

Do not implement until Codexify live integration justifies them.

---

## Tag

```bash
git tag v0.1.0rc1
```

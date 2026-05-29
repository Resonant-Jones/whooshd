# Codexify ↔ Whoosh'd Live Integration Rehearsal

Runbook and findings for connecting Codexify to a live Whoosh'd MLX server.

---

## Status

- **Completed:** Runbook prepared, Whoosh'd MLX preflight validated
- **Completed:** Live Codexify ↔ Whoosh'd rehearsal finished end to end
- **Result:** Successful — Codexify local provider is selectable/executable and the assistant response persisted

---

## Environment

- **Date:** 2026-05-29
- **Whoosh'd commit:** ad43c279714cd83facd7408febb917a0da6d0d7f (Phase 5C.1 inventory alignment)
- **Codexify commit:** f603d0a929867b9e0b5ecad778419813bcf9eb6b (Phase 5C.3 context assembly fix)
- **Machine:** Apple Silicon M-series
- **Chip:** (to be filled)
- **RAM:** ~32 GB (reported by Whoosh'd runtime)
- **Python:** 3.14.3
- **macOS:** 15.x
- **Whoosh'd adapter:** mlx
- **Whoosh'd model:** `mlx-community/Llama-3.2-3B-Instruct-4bit` (4-bit)
- **Codexify mode:** local-only
- **Codexify local provider base URL:** `http://host.docker.internal:8000/v1` (if Codexify runs in Docker) or `http://localhost:8000/v1`

---

## Preflight

Validated in isolation (Whoosh'd running, Codexify not connected):

| Check | Result | Notes |
|---|---|---|
| Whoosh'd starts with MLX | ✅ | `WHOOSHD_ADAPTER=mlx` |
| Model warmup succeeds | ✅ | ~56s first load, instant from cache |
| `/health` | 200, `lifecycle: ready` | |
| `/ready` | 200, `ready: true` | |
| `/runtime/model` | `lifecycle: ready`, `loaded: true` | |
| `/v1/models` | Returns usable model inventory | |
| `/api/tags` | Returns Ollama-compatible tags | |
| Non-streaming chat (curl) | ✅ | `chat.completion`, finish=stop |
| Streaming chat (curl) | ✅ | Token-by-token → `[DONE]` |
| `active_jobs` cleanup | ✅ | Returns to 0 |
| Admission control (overload) | ✅ | Structured 429 at limit |

Inventory note: both inventory endpoints must advertise the exact configured
model id before warmup. For this rehearsal that means
`mlx-community/Llama-3.2-3B-Instruct-4bit`, not a stale alias.

---

## Rehearsal Tests

| Test | Result | Notes |
|---|---|---|
| Whoosh'd starts with MLX | ✅ | Verified in isolation and in live rehearsal |
| Model warmup succeeds | ✅ | Verified in isolation and before live rehearsal |
| Codexify can discover local model | ✅ | Catalog exposed the configured MLX model |
| Single Codexify chat turn streams | ✅ | `task.progress` and `task.chunk` emitted |
| Assistant response persists | ✅ | Assistant message `12412` persisted |
| Two concurrent Codexify turns complete | Not run | Not required for the final proof point |
| Whoosh'd `active_jobs` returns to 0 | ✅ | `GET /runtime/requests` returned `active_count: 0` |
| Intentional overload returns structured 429 | Not run | Parked for later throughput validation |
| Codexify handles 429 as busy/degraded, not offline | Not run | Parked for later throughput validation |
| Codexify handles readiness 503 correctly | Not run | Parked for later throughput validation |

---

## Configuration Reference

### Whoosh'd Startup

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 0.0.0.0 --port 8000
```

### Warmup

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
curl -i http://localhost:8000/ready
```

### Codexify Configuration (Expected)

```bash
LLM_PROVIDER=local
CODEXIFY_LOCAL_ONLY_MODE=true
ALLOW_CLOUD_PROVIDERS=false

LOCAL_BASE_URL=http://host.docker.internal:8000/v1
LOCAL_CHAT_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_LLM_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
DEFAULT_LOCAL_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_API_KEY=local
```

**Note:** Use `http://localhost:8000` if Codexify and Whoosh'd share the same process namespace (no Docker). Use `http://host.docker.internal:8000/v1` if Codexify runs in a Docker container on the macOS host and Whoosh'd is bound to `0.0.0.0`.

---

## Rehearsal Steps

### Step 1: Start Whoosh'd with MLX

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 0.0.0.0 --port 8000
```

### Step 2: Warm model

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
curl -i http://localhost:8000/ready
```

Expected: `200`, `"ready": true`.

### Step 3: Preflight from Codexify container

```bash
curl -s http://host.docker.internal:8000/health
curl -i http://host.docker.internal:8000/ready
curl -s http://host.docker.internal:8000/v1/models
curl -s http://host.docker.internal:8000/api/tags
```

If these fail while Whoosh'd is running, the issue is networking (Docker host resolution, port binding, firewall).

### Step 4: Configure Codexify local provider

Set environment variables from the Configuration Reference above and restart Codexify.

### Step 5: Single chat turn

Send one simple chat from Codexify. Use a generic prompt:

> Say hello from the local Whoosh'd provider in one short sentence.

Record: streaming chunks visible, response persisted, no errors.

Live proof: the final rehearsal completed end to end, the assistant response persisted as message `12412`, and `GET /runtime/requests` returned `active_count: 0`.

### Step 6: Concurrent chat turns

Send two chat turns in quick succession.

Record: both complete, no 429, `active_jobs` returns to 0, Codexify marks both as successful.

### Step 7: Intentional overload (optional)

Temporarily set `WHOOSHD_MAX_ACTIVE_REQUESTS=1` on the Whoosh'd side:

```bash
WHOOSHD_MAX_ACTIVE_REQUESTS=1 python -m uvicorn whooshd.app:app ...
```

Send two or more Codexify chat turns close together.

Observe: does Codexify receive `429 RUNNER_OVERLOADED`? Does it retry, back off, or fail?

### Step 8: Readiness failure (optional)

Start Whoosh'd with MLX but do NOT warm the model. `/ready` will return `503 model_unloaded`.

Observe: does Codexify attempt chat anyway (triggering lazy load)? Or does it reject with an error? Or does it mark the provider offline?

This tells us how Codexify consumes readiness.

---

## Observations

- Live Codexify integration completed successfully.
- Provider boundary stayed `local`; display/vendor metadata resolved to `Whoosh'd` / `whooshd`.
- The configured MLX model remained the exact advertised inventory entry, so Codexify could enable the local provider without loosening validation.

---

## Failures

- No live-blocking Whoosh'd failures were observed in the final rehearsal.

---

## Required Fixes

- None for the live integration path. Keep the inventory contract exact and leave queue/batching parked.

---

## Recommended Next Action

Live rehearsal succeeded. Recommended next action:

### If integration succeeds:

> Proceed to limited local use. Whoosh'd is ready as a Codexify local provider for single-user workloads, with concurrency and overload testing still parked.

### If Codexify needs retry/readiness patch:

> Document the required Codexify-side change. Do not implement a Whoosh'd queue as a workaround.

### If Whoosh'd needs integration fix:

> Fix the specific integration bug. Do not rearchitect the adapter or API.

### If networking blocks access:

> Resolve Docker/host networking before retesting. Whoosh'd MLX path is proven in isolation.

---

## Privacy Notes

- Do not include private prompts in this document.
- Do not include generated private content.
- Do not include secrets, API keys, or tokens.
- Record content length and status — not the text itself.

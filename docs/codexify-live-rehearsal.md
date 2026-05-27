# Codexify ↔ Whoosh'd Live Integration Rehearsal

Runbook and findings for connecting Codexify to a live Whoosh'd MLX server.

---

## Status

- **Completed:** Runbook prepared, Whoosh'd MLX preflight validated
- **Blocked:** Codexify runtime not available in current execution environment
- **Result:** Pending — runbook ready for execution when Codexify is available

---

## Environment

- **Date:** 2026-05-17
- **Whoosh'd commit:** de39d42 (Phase 4F)
- **Codexify commit:** (to be filled during rehearsal)
- **Machine:** Apple Silicon M-series
- **Chip:** (to be filled)
- **RAM:** ~32 GB (reported by Whoosh'd runtime)
- **Python:** 3.14.3
- **macOS:** 15.x
- **Whoosh'd adapter:** mlx
- **Whoosh'd model:** `mlx-community/Llama-3.2-3B-Instruct-4bit` (4-bit)
- **Codexify mode:** local-only
- **Codexify local provider base URL:** `http://host.docker.internal:8000` (if Docker) or `http://localhost:8000`

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
| Whoosh'd starts with MLX | Pending | See preflight — isolation verified |
| Model warmup succeeds | Pending | Preflight verified |
| Codexify can discover local model | Pending | Requires live Codexify |
| Single Codexify chat turn streams | Pending | Requires live Codexify |
| Assistant response persists | Pending | Requires live Codexify |
| Two concurrent Codexify turns complete | Pending | Requires live Codexify |
| Whoosh'd `active_jobs` returns to 0 | Pending | Benchmarks verified concurrency 2 |
| Intentional overload returns structured 429 | Pending | Benchmarks verified at concurrency 4/8 |
| Codexify handles 429 as busy/degraded, not offline | Pending | Requires live Codexify |
| Codexify handles readiness 503 correctly | Pending | Requires live Codexify |

---

## Configuration Reference

### Whoosh'd Startup

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
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

LOCAL_BASE_URL=http://host.docker.internal:8000
LOCAL_CHAT_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_LLM_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
DEFAULT_LOCAL_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_API_KEY=local
```

**Note:** Use `http://localhost:8000` if Codexify and Whoosh'd share the same process namespace (no Docker). Use `http://host.docker.internal:8000` if Codexify runs in a Docker container on the macOS host.

---

## Rehearsal Steps

### Step 1: Start Whoosh'd with MLX

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
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

*To be filled during rehearsal.*

---

## Failures

*To be filled during rehearsal.*

---

## Required Fixes

*To be filled during rehearsal.*

---

## Recommended Next Action

Pending live rehearsal. Expected outcomes:

### If integration succeeds:

> Proceed to limited local use. Whoosh'd is ready as a Codexify local provider for single-user/concurrency-2 workloads.

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

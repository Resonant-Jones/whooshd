# Codexify Compatibility Requirements for Whoosh'd

Architectural reference for the contract Whoosh'd must uphold as a
Codexify-compatible local inference provider.

---

## Provider Model

Codexify treats Whoosh'd as a **local provider** — same category as
Ollama, llama.cpp, or CoreML.  Whoosh'd presents an OpenAI-compatible
API surface with additional runtime endpoints that Codexify uses for
health, readiness, and model discovery.

```text
Codexify (Swift / desktop / mobile)
  │
  ├─ ModelRouter
  │     └─ ProviderType.local
  │           └─ LocalProviderAdapter
  │                 └─ HTTP → Whoosh'd (127.0.0.1:8000)
  │
Whoosh'd (Python / FastAPI)
  │
  ├─ Adapter factory (stub, mlx-lm, llama.cpp)
  ├─ RuntimeState (lifecycle, health, requests)
  └─ Backend
```

---

## Required Endpoints

| Endpoint | Required | Purpose |
|---|---|---|
| `GET /health` | **Yes** | Process liveness |
| `GET /ready` | **Yes** | Inference readiness (200/503) |
| `GET /v1/models` | **Yes** | OpenAI-compatible model inventory |
| `GET /api/tags` | **Yes** | Ollama-compatible model inventory |
| `POST /v1/chat/completions` | **Yes** | OpenAI-compatible chat (streaming + non) |
| `POST /runtime/model/warmup` | Strongly rec. | Explicit model warmup |
| `POST /runtime/model/unload` | Optional | Manual unload / memory control |
| `GET /runtime/model` | Recommended | Model lifecycle metadata |

---

## Chat Completions Contract

### Non-streaming

```json
{
  "model": "<model-id>",
  "messages": [{"role": "user", "content": "..."}],
  "stream": false,
  "max_tokens": 256
}
```

Response must conform to OpenAI `v1/chat/completions` schema.

### Streaming (SSE)

Streaming responses must use **Server-Sent Events** with `data:`-prefixed
chunks and `data: [DONE]` termination:

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"}},...]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Codexify parses only `data:` lines, stops at `[DONE]`, and extracts
`choices[0].delta.content`.  No reasoning, metadata, or internal fields
should appear in the visible text stream.

---

## Model Inventory

`GET /v1/models` must return OpenAI-compatible inventory.  The model id
must exactly match the configured model:

```json
{
  "object": "list",
  "data": [
    {
      "id": "<model-id>",
      "object": "model",
      "owned_by": "whooshd"
    }
  ]
}
```

`GET /api/tags` must return Ollama-compatible inventory with matching ids.

Codexify uses this to validate `LOCAL_CHAT_MODEL` against the provider's
advertised models.  Mismatched or missing model ids will cause Codexify
to reject the provider.

---

## Health and Readiness

### Liveness (`GET /health`)

Returns `200` as long as the Whoosh'd process is alive — even during
model warmup or after a load failure.

### Readiness (`GET /ready`)

| State | Status | Meaning |
|---|---|---|
| `ready` | 200 | Model loaded, accepting chat requests |
| `warming` | 503 | Model loading; retry |
| `unloaded` | 503 | Alive but no model; trigger warmup |
| `degraded` | 503 | Alive but partially functional |
| `failed` | 503 | Model load failed; do not retry unchanged |
| `offline` | 503 | Transport or process-level failure |

**Critical rule:** `503` readiness is **not** a provider outage.
It is a "try again later" signal.  Codexify must distinguish readiness
503 from transport offline to avoid unnecessary fallback to cloud
providers.

---

## Overload and Admission

When Whoosh'd reaches its active request limit, it returns:

```http
HTTP/1.1 429 Too Many Requests
```

With a structured body:

```json
{
  "code": "RUNNER_OVERLOADED",
  "message": "Too many active requests",
  "details": {"active_jobs": 3, "max_concurrent": 2}
}
```

### Retry / Backoff Expectations

| Response | Meaning | Codexify behavior |
|---|---|---|
| `200 /health` | Process alive | Provider reachable |
| `200 /ready` | Ready for inference | Allow local inference |
| `503 /ready model_unloaded` | Alive, not loaded | Trigger warmup or show loading |
| `503 /ready model_warming` | Alive, loading | Wait/retry readiness |
| `503 /ready model_load_failed` | Alive, load failed | Mark degraded; don't call offline |
| `429 RUNNER_OVERLOADED` | At active limit | Retry/backoff or surface busy |
| `400 prompt too large` | Policy rejection | Do not retry unchanged |
| `400 max_tokens too high` | Policy rejection | Lower max_tokens |
| `5xx` | Runtime/adapter failure | Mark degraded and inspect logs |

**Core rule:**

```
429 is capacity pressure, not provider death.
503 readiness is not transport offline.
Warmup is not offline.
```

---

## Cancellation

`POST /runtime/requests/{id}/cancel` allows Codexify to cancel an
in-flight request.  Whoosh'd must:

1. Accept the cancellation request
2. Stop generating tokens for the cancelled request
3. Send a terminal SSE chunk with `finish_reason: "cancelled"` (streaming)
   or return a partial response with cancelled status (non-streaming)

---

## Concurrency

Whoosh'd must handle concurrent chat requests.  The admission control
layer enforces a configurable `max_concurrent` limit.  Requests beyond
the limit receive structured `429` responses.

---

## Large Context

Whoosh'd must support configurable max token limits via
`WHOOSHD_MAX_REQUEST_MAX_TOKENS` (default `32768`).  Requests exceeding
the limit are rejected at admission with a clear reason code.

---

## Local-First Privacy Posture

- No telemetry by default
- No cloud dependency
- All inference stays on-device
- No prompt or message leakage in runtime snapshots (metadata-only)

---

## Environment Variables Required by Codexify

```bash
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8000
LOCAL_CHAT_MODEL=<model-id>
LOCAL_LLM_MODEL=<model-id>
DEFAULT_LOCAL_MODEL=<model-id>
LOCAL_API_KEY=local
```

---

## Verification Checklist

```
[ ] Start Whoosh'd with target adapter
[ ] Warm model (POST /runtime/model/warmup)
[ ] Confirm /ready returns 200
[ ] Configure Codexify LOCAL_BASE_URL to http://localhost:8000
[ ] Configure Codexify LOCAL_CHAT_MODEL to match configured model
[ ] Confirm /v1/models returns usable model inventory
[ ] Send simple Codexify chat turn
[ ] Confirm streaming tokens appear in Codexify UI
[ ] Confirm assistant response persisted by Codexify
[ ] Run two concurrent Codexify chat turns
[ ] Confirm Whoosh'd active_jobs returns to 0 (GET /health)
[ ] Trigger overload intentionally (high concurrency, short prompt)
[ ] Confirm Codexify handles 429 without treating provider as offline
[ ] Check /runtime/admission counters: accepted/rejected counts make sense
```

---

## See Also

- [Codexify Integration Guide](codexify-integration.md)
- [Codexify Drop-In Smoke Test](codexify-drop-in-smoke-test.md)
- [Codexify Live Rehearsal Runbook](codexify-live-rehearsal.md)
- [Codexify Runtime Contract Review](codexify-runtime-contract-review.md)
- [Codexify Managed Sidecar Provider](codexify-managed-sidecar-provider.md)

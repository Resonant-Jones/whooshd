# Codexify ↔ Whoosh'd Integration Guide

How to run Codexify against Whoosh'd as a local inference provider.

---

## Architecture

```text
Codexify (Swift / desktop / mobile)
  │
  ├─ ModelRouter
  │     └─ ProviderType.local
  │           └─ MLXProviderAdapter
  │                 └─ HTTP → Whoosh'd (127.0.0.1:8000)
  │
Whoosh'd (Python / FastAPI)
  │
  ├─ Adapter factory (stub or mlx-lm)
  ├─ RuntimeState (lifecycle, health, requests)
  └─ MLX / stub backend
```

Codexify treats Whoosh'd as a **local provider** — same category as Ollama, llama.cpp, or CoreML.  
Whoosh'd presents an OpenAI-compatible API surface with additional runtime endpoints.

---

## Required Whoosh'd Endpoints

| Endpoint                     | Codexify needs it? | Purpose                           |
| ---------------------------- | ------------------ | --------------------------------- |
| `GET /health`                | Yes                | Process liveness                  |
| `GET /ready`                 | Strongly rec.      | Inference readiness               |
| `GET /runtime`               | Recommended        | Runtime metadata                  |
| `GET /runtime/model`         | Recommended        | Model lifecycle metadata          |
| `POST /runtime/model/warmup` | Recommended        | Explicit model warmup             |
| `POST /runtime/model/unload` | Optional           | Manual unload / memory control    |
| `GET /v1/models`             | Yes                | OpenAI-style model inventory      |
| `GET /api/tags`              | Yes                | Ollama-compatible model inventory |
| `POST /v1/chat/completions`  | Yes                | OpenAI-compatible chat            |

---

## Starting Whoosh'd

### Stub mode (always works, no models)

```bash
WHOOSHD_ADAPTER=stub \
python -m uvicorn whooshd.app:app --reload
```

### MLX mode (requires mlx-lm and a converted model)

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --reload
```

### Production-ish local binding

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

**Defaults:**
- `WHOOSHD_ADAPTER` defaults to `stub`
- `WHOOSHD_MLX_MODEL` defaults to `mlx-community/Llama-3.2-3B-Instruct-4bit`
- `WHOOSHD_MLX_MAX_TOKENS_DEFAULT` defaults to `256`
- `WHOOSHD_MLX_TRUST_REMOTE_CODE` defaults to `false`
- Server binds to `127.0.0.1:8000` by default (uvicorn default)
- No telemetry, no cloud dependency

---

## Codexify Environment Configuration

### Stub provider (for testing the integration)

```bash
# Point Codexify at Whoosh'd
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8000
LOCAL_CHAT_MODEL=stub-model
LOCAL_LLM_MODEL=stub-model
DEFAULT_LOCAL_MODEL=stub-model
LOCAL_API_KEY=local
```

### MLX provider (for real local inference)

```bash
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8000
LOCAL_CHAT_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_LLM_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
DEFAULT_LOCAL_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
LOCAL_API_KEY=local
```

### Docker note

```text
Use host.docker.internal when Codexify runs inside Docker and Whoosh'd runs
on the macOS host.

Use http://localhost:8000 only when Codexify and Whoosh'd resolve localhost
to the same network namespace (same machine, no containerisation).
```

---

## Health vs Readiness

Whoosh'd distinguishes two concerns:

| Concept    | Endpoint    | Meaning                                                |
| ---------- | ----------- | ------------------------------------------------------ |
| **Liveness** | `GET /health` | Is the Whoosh'd process alive and reachable?           |
| **Readiness** | `GET /ready`  | Is the model loaded and ready to accept chat requests? |

**`/health` returns 200** as long as the process is running — even during model warmup or after a load failure.

**`/ready` returns:**
- `200` when the model is loaded and ready
- `503` when the process is alive but the model is warming, unloaded, failed, or degraded

Codexify should use `/health` for liveness checks and `/ready` (or `/runtime/model`) for inference routing decisions.  
A `503` from `/ready` is **not** a provider outage — it is a "try again later" signal.

---

## Model Lifecycle

### Check model state

```bash
curl -s http://localhost:8000/runtime/model | python3 -m json.tool
```

### Trigger warmup

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
```

Codexify's warmup worker can call this before sending user chat requests.  
For MLX, the first warmup downloads/converts the model if not cached locally; subsequent warmups are instant.  
For stub, warmup is an instant no-op.

### Unload (free memory)

```bash
curl -X POST http://localhost:8000/runtime/model/unload
```

Returns `409 Conflict` if active requests are in progress.  
For MLX, releases model/tokenizer references and hints the garbage collector.

---

## Streaming Expectations

Whoosh'd returns OpenAI-compatible SSE chunks:

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"}},...]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Codexify's `MLXRunnerClient` parses only `data:` lines, stops at `[DONE]`, and extracts `choices[0].delta.content`.  
No reasoning, metadata, or internal fields are included in the visible text stream.

---

## Manual Verification Sequence

Run these against a running Whoosh'd server to validate the integration.

### 1. Liveness

```bash
curl -s http://localhost:8000/health
```

Expected: `{"ok": true, ...}`

### 2. Readiness

```bash
curl -i http://localhost:8000/ready
```

Expected: `200` when ready, `503` when warming/unloaded/failed.

### 3. Model lifecycle

```bash
curl -s http://localhost:8000/runtime/model | python3 -m json.tool
```

### 4. Warmup

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
```

### 5. Model inventory

```bash
curl -s http://localhost:8000/v1/models | python3 -m json.tool
curl -s http://localhost:8000/api/tags | python3 -m json.tool
```

### 6. Non-streaming chat

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stub-model",
    "messages": [{"role":"user","content":"Say hello from Whooshd."}],
    "stream": false,
    "max_tokens": 64
  }'
```

### 7. Streaming chat

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stub-model",
    "messages": [{"role":"user","content":"Say hello from Whooshd."}],
    "stream": true,
    "max_tokens": 64
  }'
```

Expected: `data:`-prefixed SSE lines, terminating with `data: [DONE]`.

---

## Provider Smoke Probe

A test-facing utility validates the full provider contract:

```python
from whooshd.compat.probe_server import smoke_test_server
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
    result = await smoke_test_server(client)
    print(f"ok={result.ok}, ready={result.ready}, errors={result.errors}")
```

The probe checks: health, readiness, model inventory (OpenAI + Ollama), non-streaming chat, and streaming chat with visible text reconstruction.  
It never prints prompts, messages, or generated text by default.

---

## Known Unsupported Features

The following are **not yet implemented or not production-hardened:**

- Embeddings endpoint
- Tool calling / function calling
- Vision / multimodal MLX requests
- Queue / admission control
- Request batching / continuous batching
- Prompt prefix caching
- ThreadWake persistent KV cache
- Multi-model concurrent routing
- Production authentication hardening
- Real model-level cancellation (request lifecycle tracking exists; backend interruption is not hardened)

---

## Troubleshooting

| Symptom                            | Likely cause                                  |
| ---------------------------------- | --------------------------------------------- |
| `/ready` returns `503`             | Model is warming, unloaded, or failed. Check `/runtime/model`. |
| `/ready` returns `503` after MLX start | First MLX run may be downloading the model. Wait for warmup to complete. |
| `/v1/chat/completions` returns `501` for streaming | MLX streaming requires Phase 1F+ adapter. Stub streaming always works. |
| `/runtime/model/unload` returns `409` | Active requests are running. Wait for them to complete. |
| `mlx_lm` import error              | MLX adapter selected but `mlx-lm` not installed. `pip install mlx-lm` or use stub. |
| Model download on first MLX run    | Expected. The model is cached in `~/.cache/huggingface/` after first load. |

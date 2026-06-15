# Manual Runtime Validation Guide

How to manually validate both Whoosh'd runtime lanes (llama.cpp and MLX-LM Server).

## Prerequisites

* Whoosh'd installed (`pip install -e ".[dev]"`)
* One or both of:
  * llama.cpp server running with a GGUF model
  * `mlx_lm.server` installed (`pip install mlx-lm`)

## Start Whoosh'd

```bash
# With stub adapter (no models needed — for baseline testing)
WHOOSHD_ADAPTER=stub python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000

# With MLX-LM Server (subprocess-supervised)
WHOOSHD_MLX_ENABLED=true \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  WHOOSHD_MLX_PORT=8081 \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000

# With Gemma E2B through MLX-LM Server and validated aliases
WHOOSHD_MLX_ENABLED=true \
  WHOOSHD_MLX_MODEL=mlx-community/gemma-4-e2b-it-4bit \
  WHOOSHD_MLX_PORT=8081 \
  WHOOSHD_MODEL_REGISTRY_PATH=configs/models.validated.yaml \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000

# With llama.cpp (external server)
WHOOSHD_ADAPTER=llama_cpp \
  WHOOSHD_LLAMA_CPP_SERVER_URL=http://127.0.0.1:8080 \
  WHOOSHD_LLAMA_CPP_MODEL_PATH=/path/to/model.gguf \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Health Checks

### Liveness
```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"ok": true, "runner": "whooshd", ...}`

### Per-runtime health
```bash
curl http://127.0.0.1:8000/health/runtime
```

Expected: JSON with per-runtime state, e.g.:
```json
{
  "status": "ok",
  "runtimes": {
    "stub": {
      "kind": "stub",
      "enabled": true,
      "state": "ready",
      "active_model": "stub-model"
    }
  }
}
```

### Readiness
```bash
curl http://127.0.0.1:8000/ready
```

Expected: 200 when ready, 503 when model is warming/unloaded.

## Model Inventory

### OpenAI-compatible
```bash
curl http://127.0.0.1:8000/v1/models
```

Expected: OpenAI-style model list with `data` array.

### Ollama-compatible
```bash
curl http://127.0.0.1:8000/api/tags
```

Expected: Ollama-style model list with `models` array.

## Model Registry (optional)

When a registry is configured, model inventory includes all registered models:

```bash
WHOOSHD_MODEL_REGISTRY_PATH=configs/models.yaml \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

```bash
curl http://127.0.0.1:8000/v1/models
# Should show both GGUF and MLX models from the registry.
```

When the validated registry is used with
`WHOOSHD_MLX_MODEL=mlx-community/gemma-4-e2b-it-4bit`, `/v1/models` and
`/api/tags` should show `gemma-4-e2b-mlx` for the active MLX text lane. Use
`model: "gemma-4-e2b-mlx"` in OpenAI-compatible chat requests. `/health/runtime`
continues to report the raw configured model id
`mlx-community/gemma-4-e2b-it-4bit`.

## Chat Completions

### Non-streaming (stub)
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stub-model",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": false
  }'
```

### Streaming (stub)
```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stub-model",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": true
  }'
```

Expected: Server-Sent Events (SSE) with `text/event-stream` content type.

### Non-streaming (MLX-LM Server)
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": false
  }'
```

### Streaming (MLX-LM Server)
```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": true
  }'
```

### Non-streaming (llama.cpp)
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "/path/to/model.gguf",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": false
  }'
```

### Streaming (llama.cpp)
```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "/path/to/model.gguf",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": true
  }'
```

## Error Scenarios

### Model not found (400)
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nonexistent-model",
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": false
  }'
```

### Runtime unavailable (503)
When no runtime is configured for a model, or the upstream server is down, Whoosh'd returns 503 with an appropriate error body.

## Auto-generated API Docs

Open http://127.0.0.1:8000/docs for the Swagger UI.
Open http://127.0.0.1:8000/redoc for ReDoc.

---

## Phase 3 Real Runtime Validation Results

### Machine Info

| Field | Value |
|-------|-------|
| Machine | Apple Silicon (Mac) |
| OS | macOS (darwin) |
| Python | 3.14.3 |
| Whoosh'd version | 0.1.0rc1 |

### Validation Status

| Runtime | Status |
|---------|--------|
| llama.cpp (real) | **not tested** — blocked; no llama-server binary available in this environment |
| llama.cpp (mock) | ✅ mock-tested — 519 tests pass with mocked upstream |
| MLX-LM Server (real) | **not tested** — blocked; mlx-lm package not installed in this environment |
| MLX-LM Server (mock) | ✅ mock-tested — all adapter forwarding tests pass |

### Test Results Summary

| Runtime | Model | Streaming | Non-streaming | /v1/models | /api/tags | /health/runtime | Result |
|---------|-------|-----------|---------------|------------|-----------|-----------------|--------|
| llama.cpp (mock) | test.gguf | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | mock-tested only |
| MLX-LM Server (mock) | mlx-community/test | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | mock-tested only |
| Stub | stub-model | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS | real-tested |

### Real Runtime Validation Commands (when ready)

Once a real llama.cpp server or MLX-LM Server is available, use these commands:

```bash
# ── llama.cpp ────────────────────────────────────────────────────────
# Start llama-server (external mode)
llama-server --model /path/to/model.gguf --host 127.0.0.1 --port 8080

# Start Whoosh'd pointing at it
WHOOSHD_ADAPTER=llama_cpp \
  WHOOSHD_LLAMA_CPP_SERVER_URL=http://127.0.0.1:8080 \
  WHOOSHD_LLAMA_CPP_MODEL_PATH=/path/to/model.gguf \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000

# ── MLX-LM Server ───────────────────────────────────────────────────
# Start mlx_lm.server directly
python -m mlx_lm.server --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --host 127.0.0.1 --port 8081

# Start Whoosh'd with MLX-LM Server lane enabled
WHOOSHD_MLX_ENABLED=true \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  WHOOSHD_MLX_HOST=127.0.0.1 \
  WHOOSHD_MLX_PORT=8081 \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

### Validation Commands (to run against both runtimes)

```bash
# Health
curl http://127.0.0.1:8000/health/runtime | jq .

# Models
curl http://127.0.0.1:8000/v1/models | jq .

# Non-streaming chat
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<your-model>","messages":[{"role":"user","content":"Say hello."}],"stream":false}' | jq .

# Streaming chat
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"<your-model>","messages":[{"role":"user","content":"Say hello."}],"stream":true}'
```

---

## Compatibility Notes

### Mid-Stream Error Behavior

When an upstream runtime fails during SSE streaming (e.g., timeout, connection lost, server crash), Whoosh'd emits a JSON error chunk inside the SSE stream:

```
data: {"error": {"message": "...", "type": "UpstreamTimeoutError"}}
```

This is followed by stream termination (no `[DONE]` sentinel).

**Client compatibility:**
- OpenAI-compatible clients: Most clients stop processing on any non-chunk line. The error chunk provides useful diagnostics.
- Codexify: Codexify's SSE parser expects `choices[0].delta.content`. On an error chunk, it will skip the line (no `choices` field) and wait for more data. The stream terminate causes a read timeout or incomplete response.
- **Recommendation**: The current behavior is acceptable for diagnostics. A future enhancement could yield the error as a regular JSON response before the SSE stream starts (for errors detected during request setup) while keeping the SSE error chunk for mid-stream failures.

### /v1/generate Compatibility

The `/v1/generate` endpoint converts the prompt to a single-message chat completion and maps the response back to `GenerateResponse` format:

```
GenerateRequest → ChatCompletionRequest → upstream → ChatCompletionResponse → GenerateResponse
```

**Tradeoffs:**
- ✅ Works with both llama.cpp (no dedicated `/v1/generate` endpoint) and `mlx_lm.server`
- ✅ Response format is consistent (Codexify-style `GenerateResponse`)
- ⚠️ Prompt formatting may differ from direct generate (chat template applied by upstream)
- ⚠️ Token counts are estimated from chat completion usage
- ⚠️ No streaming support for generate yet

**When to implement direct generate support:**
- If a backend exposes `/v1/generate` and Codexify depends on it for specific prompt formats
- Not currently required — Codexify primarily uses `/v1/chat/completions`

### Request Field Forwarding

All OpenAI-compatible request fields are preserved. See `tests/test_forwarding.py::TestFieldPreservation` for the full list.

**Note on tools/function calling**: Tool-related fields (`tools`, `tool_choice`, `parallel_tool_calls`) are forwarded. But Whoosh'd does NOT invent tool-calling behavior. The upstream runtime must support tool calling for it to work. Test by sending a request with `tools` to a capable model/backend.

### Codexify Streaming Compatibility

Verified against stub adapter. See `tests/test_codexify_stream_compat.py`:
- ✅ Chunks have `id`, `object`, `created`, `model`, `choices`
- ✅ First chunk has `delta.role: "assistant"`
- ✅ Content deltas in subsequent chunks
- ✅ Final chunk has `finish_reason: "stop"`
- ✅ Stream ends with `data: [DONE]`
- ✅ Codexify-style parser successfully reconstructs full text

---

## Recommended Next Phase

1. **Real runtime validation**: Install `mlx-lm` and `llama.cpp`, run the manual validation commands above, and update this document.
2. **Subprocess management integration**: Test `WHOOSHD_MLX_ENABLED=true` with Whoosh'd managing the `mlx_lm.server` subprocess (currently the managed process path exists but needs real-runtime testing).
3. **Tool calling end-to-end**: Test tool-calling with a capable model through both runtime lanes.
4. **Streaming generate**: Add streaming support to `/v1/generate` if needed by Codexify.
5. **Concurrent request handling**: Validate behavior under concurrent streaming requests.
6. **Response body validation**: Add schema validation of upstream responses to catch malformed chunks before they reach clients.

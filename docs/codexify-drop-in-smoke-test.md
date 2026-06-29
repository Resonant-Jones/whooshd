# Codexify Drop-In Smoke Test Guide

How to configure Codexify to use Whoosh'd as a local inference provider,
and validate the integration.

## Prerequisites

* Whoosh'd running (see [manual-runtime-validation.md](manual-runtime-validation.md))
* Codexify installed

## Step 1: Start Whoosh'd

```bash
# Default local MLX path
whoosh -d
whoosh status

# Alternate start entrypoints
whoosh up
whooshd up
whooshd-up
```

One implementation, multiple affordance routes. `whoosh -d`, `whoosh up`,
`whooshd up`, and `whooshd-up` all start the same server path. `whoosh down`,
`whooshd down`, and `whooshd-down` all stop the same tracked process.

Developer/debug startup remains available when you need explicit runtime
selection:

```bash
# With llama.cpp
WHOOSHD_ADAPTER=llama_cpp \
  WHOOSHD_LLAMA_CPP_SERVER_URL=http://127.0.0.1:8080 \
  WHOOSHD_LLAMA_CPP_MODEL_PATH=/path/to/model.gguf \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Step 2: Verify Whoosh'd Health

```bash
# Basic liveness
curl http://127.0.0.1:8000/health | jq .

# Per-runtime health
curl http://127.0.0.1:8000/health/runtime | jq .

# Readiness
curl http://127.0.0.1:8000/ready | jq .
```

Expected: `/health` returns `ok: true`. `/health/runtime` shows at least one runtime in `ready` state. `/ready` returns 200.

## Step 3: Verify Model Inventory

```bash
# OpenAI-compatible
curl http://127.0.0.1:8000/v1/models | jq .

# Ollama-compatible
curl http://127.0.0.1:8000/api/tags | jq .
```

Expected: Both endpoints return at least one model. Note the model ID(s) — you'll need them for step 5.

## Step 4: Codexify Local Provider Configuration

Codexify uses environment variables to configure its local provider connection.
Create or update your Codexify environment:

```env
# Codexify local provider configuration for Whoosh'd
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8000
LOCAL_CHAT_MODEL=<whooshd-model-name>
LOCAL_API_KEY=local
```

Replace `<whooshd-model-name>` with the model ID from step 3.

If Codexify uses different environment variable names, check its documentation
for the correct variable names. Common alternatives:

```env
# Alternative variable names seen in some Codexify versions
CODEXIFY_LLM_PROVIDER=local
CODEXIFY_LOCAL_BASE_URL=http://127.0.0.1:8000
CODEXIFY_LOCAL_MODEL=<whooshd-model-name>
CODEXIFY_LOCAL_API_KEY=local
```

## Step 5: Send a Test Chat Turn

### Non-streaming
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<your-model-id>",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is 2+2?"}
    ],
    "stream": false
  }' | jq .
```

Expected: 200 with `choices[0].message.content` containing a valid response.

### Streaming
```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<your-model-id>",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is 2+2?"}
    ],
    "stream": true
  }'
```

Expected:
- HTTP 200
- `Content-Type: text/event-stream`
- Stream of `data: {...}` chunks
- First chunk: `"delta":{"role":"assistant"}`
- Subsequent chunks: `"delta":{"content":"..."}`
- Final chunk: `"finish_reason":"stop"`
- Stream ends with `data: [DONE]`

## Step 6: Verify Streaming Compatibility

Use the validation harness to verify Codexify-compatible streaming:

```bash
python scripts/validate_local_runtimes.py --whooshd-url http://127.0.0.1:8000 --runtime both --stream --no-non-stream
```

The "Codexify SSE compat" check in the output verifies:
- Every chunk has `id`, `object`, `created`, `model`, `choices`
- First chunk has `delta.role: "assistant"`
- At least one chunk has `finish_reason: "stop"`
- Stream ends with `data: [DONE]`

## Step 7: Verify Concurrent Streaming

Codexify uses multiple concurrent chat workers. Verify Whoosh'd handles this:

```bash
python scripts/validate_local_runtimes.py --whooshd-url http://127.0.0.1:8000 --runtime both --concurrency 2
```

Expected: All concurrent requests complete successfully with distinct outputs.
No cross-talk, no deadlocks, no stuck requests.

## Step 8: Verify Warmup Reporting

Whoosh'd must never report warmup as offline. Verify:

```bash
# Check /ready during warmup (if model is loading)
curl http://127.0.0.1:8000/ready | jq .
```

Expected during warmup: 503 with `"reason": "model_warming"`.
Expected when ready: 200 with `"ready": true`.

```bash
# Check per-runtime health
curl http://127.0.0.1:8000/health/runtime | jq .
```

Expected: Runtime state `"model_warming"` is distinct from `"offline"`.

## Troubleshooting

### "/health/runtime shows no non-stub runtimes"
Whoosh'd defaults to the stub adapter. Use `WHOOSHD_ADAPTER=llama_cpp` or
`WHOOSHD_MLX_ENABLED=true` to register real runtimes.

### "Connection refused"
Make sure Whoosh'd is running on the expected port. Check with:
```bash
curl http://127.0.0.1:8000/health
```

### "Model not found"
The model ID in your request must match a model in the inventory.
Check available models:
```bash
curl http://127.0.0.1:8000/v1/models | jq '.data[].id'
```

### "Upstream server not reachable"
If using llama.cpp or MLX-LM Server, the upstream runtime server must be
running. Start it with the provided helper scripts:
```bash
# llama.cpp
export WHOOSHD_LLAMA_CPP_BINARY_PATH=/path/to/llama-server
export WHOOSHD_LLAMA_CPP_MODEL_PATH=/path/to/model.gguf
bash scripts/start_llama_cpp_runtime.sh

# MLX-LM Server
export WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
bash scripts/start_mlx_lm_runtime.sh
```

## Related Documentation

* [Manual Runtime Validation](manual-runtime-validation.md) — curl commands for every endpoint
* [Runtime Validation Results Template](runtime-validation-results-template.md) — record your results
* [Codexify Integration Guide](codexify-integration.md) — original Codexify setup guide

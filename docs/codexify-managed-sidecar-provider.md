# Codexify ⇄ Whoosh'd Managed Sidecar Provider Contract

## Boundary

Whoosh'd is a **standalone** local inference gateway. Codexify integrates
with Whoosh'd over HTTP only. Neither project imports the other.

```
Codexify                          Whoosh'd
  ├── identity, RAG, tasks         ├── runtime orchestration
  ├── UI, user data                ├── MLX text / MLX-VLM / GGUF lanes
  ├── detects Whoosh'd over HTTP   ├── /v1/chat/completions
  ├── optionally launches sidecar  ├── /v1/models, /api/tags
  └── sends OpenAI-compatible reqs └── /health/runtime, /ready
```

Whoosh'd does NOT require Codexify. Whoosh'd can serve any
OpenAI-compatible local client.

## Required Endpoints

Codexify depends on these Whoosh'd endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Process liveness |
| GET | `/health/runtime` | Per-runtime state + session identity |
| GET | `/ready` | Inference readiness (200/503) |
| GET | `/v1/models` | OpenAI-compatible model inventory |
| GET | `/api/tags` | Ollama-compatible model inventory |
| POST | `/v1/chat/completions` | Text + vision inference (streaming + non-streaming) |

## Required Response Behavior

### Streaming

- `Content-Type: text/event-stream`
- Each line: `data: <JSON chunk>`
- Terminal line: `data: [DONE]`
- First chunk: `delta.role = "assistant"`
- Content chunks: `delta.content` with text
- Final chunk: `finish_reason = "stop"`, empty delta

### Health

- `/health/runtime` distinguishes: `offline`, `starting`, `model_warming`, `ready`, `generating`, `degraded`, `error`
- Model warmup is NOT collapsed into offline
- Runtime busy does NOT report as offline
- Session block includes: `pid`, `session_id`, `started_at`, `registered_runtime_kinds`

### Model Discovery

- `/v1/models` returns OpenAI-style `data` array
- `/api/tags` returns Ollama-compatible `models` array
- Registry aliases are preferred over raw model paths
- `stub-model` only appears when no real runtimes are registered

## Integration Modes

### 1. External Already-Running Mode

Whoosh'd is already listening on a known port.

```
Codexify → http://127.0.0.1:8000
```

Codexify validates:
- `/health` returns 200
- `/health/runtime` reports expected runtime kind
- `/v1/models` includes expected model
- `/api/tags` includes expected model

### 2. Managed Sidecar Mode

Codexify launches Whoosh'd as a child process.

```bash
WHOOSHD_MODEL_REGISTRY_PATH=configs/models.validated.yaml \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Codexify:
- Stores the PID and session_id of the launched process
- Polls `/health` until ready or timeout
- May stop ONLY the process it launched
- Must NOT kill unknown Whoosh'd processes

### 3. Manual Mode

User starts Whoosh'd independently.

Codexify only needs:
```
WHOOSHD_BASE_URL=http://127.0.0.1:8000
```

## Process Ownership Rules

1. Codexify may stop Whoosh'd ONLY if Codexify launched that process.
2. If Whoosh'd was already running, Codexify treats it as user-managed.
3. Codexify stores PID/session_id when it launches Whoosh'd.
4. On shutdown, Codexify checks PID/session_id before stopping.
5. If session_id changed (Whoosh'd restarted independently), Codexify must NOT claim ownership.
6. If port is occupied by unknown process, Codexify diagnoses and shows a clear message.

**Example error message:**
```
Whoosh'd appears to be running on port 8000, but it is not the process
Codexify started. Codexify will use it as an external provider and
will not stop it automatically.
```

## Recommended Codexify Provider Configuration

```env
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8000
LOCAL_CHAT_MODEL=llama-3.2-3b-mlx
LOCAL_VISION_MODEL=qwen2-vl-2b-mlx
LOCAL_GGUF_MODEL=qwen2.5-0.5b-gguf
LOCAL_PROVIDER_KIND=whooshd
LOCAL_API_KEY=local
```

## Validated Model Aliases

Use these clean aliases in Codexify configuration:

| Alias | Runtime | Format | Validated |
|-------|---------|--------|-----------|
| `qwen2.5-0.5b-gguf` | llama.cpp | GGUF | ✅ real |
| `llama-3.2-3b-mlx` | mlx_lm.server | MLX text | ✅ real |
| `qwen2-vl-2b-mlx` | mlx-vlm | MLX vision | ✅ real |

## Future Endpoints (Not Required Yet)

- `POST /api/chat` — Ollama-compatible chat (optional)
- `POST /v1/embeddings` — embeddings (future)
- Metrics endpoint (future)

## Non-Requirements

- Whoosh'd does NOT import Codexify internals.
- Codexify does NOT import Whoosh'd internals.
- Whoosh'd does NOT require Codexify to run.
- Whoosh'd does NOT depend on Ollama.
- Whoosh'd does NOT call or proxy to Ollama.

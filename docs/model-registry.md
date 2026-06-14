# Multi-Engine Model Registry

Why Whoosh'd now has a model registry, how it works, and what's not built yet.

---

## Why a model registry?

Whoosh'd started as a single-model MLX inference server for Apple Silicon.
That worked well but locked the project to one engine, one model, and one
hardware target.

The model registry separates **what models are available** from **how they
are served**.  This makes Whoosh'd a backend-pluggable inference control
plane that can support:

1. **MLX / mlx-vlm models** on Apple Silicon (the existing path)
2. **GGUF / llama.cpp models** on cross-platform machines (coming soon)

Adding a new engine or model format is now a registry entry, not a code
rewrite.

---

## Engine vs format — the key distinction

| Concept | Meaning | Examples |
|---|---|---|
| **Engine** | The inference runtime that loads and runs the model | `mlx_lm`, `mlx_vlm`, `llama_cpp` |
| **Format** | The file format of the model weights | `mlx`, `gguf` |

Clients calling the API never see these values directly (unless they look
at model metadata).  Whoosh'd uses them internally to route requests to
the correct engine.

### Validation rules

The registry enforces cross-field consistency:

- `gguf` format **must** route to `llama_cpp` engine
- `mlx` format **must** route to `mlx_lm` *or* `mlx_vlm` engine
- Vision models **must** use a vision-capable engine (`mlx_vlm`)

Malformed entries are rejected with a clear `RegistryValidationError`
that names the model ID and the specific rule violated.

---

## YAML file structure

The registry lives in a single YAML file.  An example is at
`configs/models.yaml`:

```yaml
models:
  # Each key is the model ID used in API calls.
  qwen3-coder-30b-gguf:
    display_name: "Qwen3 Coder 30B GGUF"
    engine: llama_cpp          # Inference engine
    format: gguf               # Model file format
    path: "/models/qwen3-coder-30b/q4_k_m.gguf"
    modalities:                # text, vision, embedding
      - text
    context_window: 32768      # Max context in tokens
    preferred_hardware:        # Affinity hints (apple_silicon, cuda, cpu, etc.)
      - cuda
      - metal
      - cpu
    warm_policy: warm_on_first_use  # cold, warm_on_start, warm_on_first_use, keep_warm
    priority: coding           # Usage class label
    enabled: true              # Set to false to disable without deleting
    tags:                      # Free-form tags
      - coding
      - gguf
      - local
```

### Field reference

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `display_name` | string | **yes** | — | Human-readable name |
| `engine` | enum | **yes** | — | `mlx_lm`, `mlx_vlm`, `llama_cpp` |
| `format` | enum | **yes** | — | `mlx`, `gguf` |
| `path` | string | **yes** | — | HF repo ID, local directory, or GGUF file path |
| `modalities` | list | no | `[text]` | One or more of: `text`, `vision`, `embedding` |
| `context_window` | int | no | `32768` | Max context window in tokens |
| `preferred_hardware` | list | no | `[auto]` | `apple_silicon`, `metal`, `cuda`, `hip`, `vulkan`, `cpu`, `auto` |
| `warm_policy` | enum | no | `warm_on_first_use` | `cold`, `warm_on_start`, `warm_on_first_use`, `keep_warm` |
| `priority` | string | no | `general` | Usage class label |
| `enabled` | bool | no | `true` | Active/inactive flag |
| `tags` | list | no | `[]` | Free-form tags |

---

## How to activate the registry

Set the `WHOOSHD_MODEL_REGISTRY_PATH` environment variable to point at
your YAML file:

```bash
export WHOOSHD_MODEL_REGISTRY_PATH=configs/models.yaml
```

When this variable is **not set**, Whoosh'd falls back to the existing
single-model behaviour driven by `WHOOSHD_ADAPTER` and `WHOOSHD_MLX_MODEL`.
This means **every existing deployment continues working without changes**.

---

## API impact

When the registry is active, model inventory endpoints reflect all
enabled models:

### `GET /v1/models` (OpenAI format)

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3-coder-30b-gguf",
      "object": "model",
      "created": 1700000000,
      "owned_by": "whooshd",
      "metadata": {
        "engine": "llama_cpp",
        "format": "gguf",
        "modalities": ["text"],
        "context_window": 32768
      }
    }
  ]
}
```

### `GET /api/tags` (Ollama format)

```json
{
  "models": [
    {
      "name": "qwen3-coder-30b-gguf",
      "model": "qwen3-coder-30b-gguf",
      "modified_at": "2024-01-01T00:00:00Z",
      "size": 1500000000,
      "details": {
        "format": "gguf",
        "family": "llama_cpp"
      }
    }
  ]
}
```

## Non-goals (not yet implemented)

This task delivered schema, configuration, registry, and validation only.
The following are **intentionally deferred** to future tasks:

- **llama.cpp process management** — no `llama-server` is launched
- **GGUF inference** — the llama_cpp adapter inference stubs raise intentional errors
- **Multi-model loading** — runtime still loads one model at a time
- **Batching** — not implemented
- **KV-cache reuse** — not implemented
- **Hardware probing** — `preferred_hardware` is a descriptive hint only
- **Cloud inference providers** — out of scope

MLX and GGUF are sibling backends.  Clients call the same
OpenAI/Ollama-compatible API regardless of which engine serves the model.

---

## llama.cpp adapter skeleton

The llama.cpp adapter (`whooshd/adapters/llama_cpp.py`) is the GGUF
execution lane for Whoosh'd.  In this phase it defines configuration,
adapter selection, health probing, and registry integration only.
Full request forwarding, streaming, batching, and process supervision
will be implemented in later tasks.

### What is implemented now

- **`LlamaCppAdapterConfig`** — typed Pydantic config for server URL, binary path, host/port, timeouts, and auto-start (default: false)
- **`LlamaCppAdapter`** — conforms to the `InferenceAdapter` protocol (`name`, `supports_streaming`, lifecycle methods)
- **Health probing** — probes a remote llama.cpp server at `/health` and `/v1/models`; maps outcomes to Whoosh'd runner/model lifecycle states
- **Factory registration** — `WHOOSHD_ADAPTER=llama_cpp` selects the llama.cpp adapter
- **Registry integration** — GGUF models carry `engine: llama_cpp` metadata in `/v1/models` and `/api/tags`
- **Request normalization placeholder** — `normalize_chat_request_for_llama_cpp()` converts OpenAI-format requests to the llama.cpp server shape

### What is intentionally not implemented yet

- **Inference** — `chat_completion`, `chat_completion_stream`, and `generate` raise `_LlamaCppNotImplementedError`
- **Process supervision** — `auto_start` exists but no subprocess is launched
- **Model loading** — no weights are loaded; `is_loaded()` returns `False`
- **Streaming** — the stub raises an error; real SSE forwarding is deferred

### How it will later connect

The adapter will forward `ChatCompletionRequest` objects to a llama.cpp
server's `/v1/chat/completions` endpoint (which is OpenAI-compatible).
The `normalize_chat_request_for_llama_cpp()` placeholder shows the
mapping shape.  When `auto_start=True`, a future process manager will
launch `llama-server` with the configured `binary_path`, `model_path`,
and `extra_args`.

---

## Model format is an internal routing concern

Whoosh'd treats model format as an internal routing concern.  Clients
call the same OpenAI/Ollama-compatible API regardless of whether a model
is backed by MLX or GGUF.  The model registry describes available local
models, their runtime engines, capabilities, context windows, warm
policies, and hardware affinity.

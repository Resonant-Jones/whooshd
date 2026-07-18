# Multi-Engine Model Registry

Why Whoosh'd now has a model registry, how it works, and what's not built yet.

## Two registries, one truth

Whoosh'd uses two complementary registry layers:

| Layer | Location | Purpose |
|-------|----------|---------|
| **Runtime registry** | `configs/models.yaml` (YAML) | Describes *configured* models for inference routing |
| **Model-store** | `~/whooshd-models/` (filesystem) | Holds user-managed model artifacts and durable manifest |

The runtime registry tells Whoosh'd *what* to serve.  The model-store
holds *where* user models live on disk and provides intake/quarantine
areas for future model management.

---

## Model-store layout

The `bootstrap_model_store()` function creates this layout:

```text
~/whooshd-models/
  incoming/         # Drop zone for new model artifacts
  models/
    mlx/            # MLX-format text models
    gguf/           # GGUF-format models (llama.cpp)
    vlm/            # MLX-format vision-language models
  registry/
    models.json     # Durable manifest (schema_version 1)
  quarantine/       # Models that failed inspection
  tmp/              # Temp workspace for atomic writes
```

### Key distinctions

| Term | Meaning |
|------|---------|
| **Candidate** | Files found on disk that may be a model |
| **Registered model** | Entry in `registry/models.json` |
| **Advertised model** | Model visible in `/v1/models` (runtime promise) |
| **Runnable model** | Model actively loaded by a runtime adapter |

A file on disk is a *candidate*, not a registered model.
A registered model is not *advertised* until the runtime registry
picks it up.
An advertised model is not *runnable* until a runtime adapter loads it.

### Why scanning is not enough

- Filesystem contents are not a model inventory — they are artifact storage.
- A `.gguf` file may be corrupt, misconfigured, or unsupported by the current llama.cpp version.
- A `.safetensors` directory may require a specific tokenizer that is missing.
- Whoosh'd must validate before advertising.
- `/v1/models` is a runtime promise, not a filesystem mirror.

---

## Candidate inspection

The `inspect_model_candidate()` function classifies a user-provided model
artifact path without copying, moving, or registering any files.

### Candidate metadata shape

```json
{
  "candidate_id": "3a7c341d86fd80e2",
  "status": "candidate",
  "source_path": "/absolute/path/to/model",
  "detected_format": "mlx",
  "detected_family": "gemma",
  "modalities": ["text"],
  "evidence": ["found_config_json", "found_tokenizer", "found_safetensors"],
  "problems": [],
  "created_at": "2026-06-14T..."
}
```

### Statuses

| Status | Meaning |
|--------|---------|
| `candidate` | Sufficient evidence of a valid model artifact |
| `unsupported` | Recognized but not a supported model format or incomplete |
| `invalid` | Path missing, unreadable, or fundamentally broken |

### Evidence and problems

Evidence strings are machine-readable tags describing what was found
(`found_config_json`, `found_safetensors`, `model_type_gemma`, etc.).
Problems describe issues (`path_missing`, `config_unreadable`,
`ambiguous_candidate`, `empty_directory`).

### Vision detection is conservative

Vision modality is only added when there is **strong explicit evidence**.
The following are **not** sufficient alone:

- `processor_config.json`
- `preprocessor_config.json`
- `tokenizer_config.json`
- `generation_config.json`
- generic `processor` metadata
- a model path containing only `gemma`
- a model path containing only `mlx`

Acceptable strong vision evidence (at least one required):

- `config.json` contains a top-level `vision_config` → evidence: `found_vision_config`
- Explicit VLM `model_type` markers (`qwen2_vl`, `qwen2-vl`, `llava`, `paligemma`) → evidence: `model_type_<name>`
- Explicit VLM architecture markers (`Qwen2VLForConditionalGeneration`, etc.) → evidence: `architecture_<name>`
- Managed directory contains `mm_projector`, `vision_tower`, or `image_newline` → evidence: `found_<name>`

When in doubt, the inspector classifies the model as **text-only**.
Vision over-claiming is worse than vision under-claiming.

### Invariant: vision requires explicit vision evidence

Whoosh'd enforces a **hard invariant** at the inspection return path:

> ``vision`` may appear in ``modalities`` **only** when ``evidence``
> contains at least one explicit vision evidence code.

This invariant is enforced by a safety-net helper that sanitizes
modalities immediately before constructing the candidate result.

If a previously registered local model was misclassified as vision-capable
before this correction, the user should **delete and re-register** that
local store entry manually.  Migration of existing user stores is out of
scope; the invariant only applies to new inspections.

### What inspection does NOT do

- Does NOT register the model as runnable.
- Does NOT expose the candidate in `/v1/models` or `/api/tags`.
- Does NOT write to `registry/models.json`.
- Does NOT copy, move, or delete any files.
- Does NOT launch or validate a runtime adapter.

Candidate records are written to `registry/candidates/<id>.json`
via `write_candidate_record()`.  They are inspection artifacts, not
runtime promises.

### Key distinctions (expanded)

| Term | Meaning |
|------|---------|
| **Candidate** | Inspected artifact that may become a registered model |
| **Registered model** | Entry in `registry/models.json` |
| **Advertised model** | Visible in `/v1/models` (runtime promise) |
| **Runnable model** | Actively loaded by a runtime adapter |

### Future phases

- Registration from candidate → `registry/models.json` entry (✅ implemented)
- Managed copy into `models/mlx`, `models/gguf`, or `models/vlm` (✅ implemented)
- Runtime advertisement from registered models (**✅ implemented**)
- Adapter compatibility validation (✅ implemented)
- Drag/drop UI intake
- Optional advanced external-reference registration

---
## Compatibility validation

The `validate_registered_model_compatibility()` function inspects a
registered model's metadata and managed files to determine which runtime
adapter it maps to and whether it is advertisable.

### Adapter mapping

| Format | Modalities | Adapter |
|--------|-----------|---------|
| MLX | text only | `mlx_lm_server` |
| MLX | text + vision | `mlx_vlm` |
| GGUF | text | `llama_cpp` |
| unknown | any | `unknown` (incompatible) |

### Advertisable vs runnable

```
registered  -> Whoosh'd knows the model exists
compatible   -> Whoosh'd knows which adapter to use
advertisable -> Whoosh'd can safely expose the model in /v1/models
runnable     -> A runtime adapter has loaded and validated the model
```

Compatibility validation is a **read-only** gate.  It does not write
to `registry/models.json`, launch adapters, or expose models in runtime
inventory.  See `whooshd/model_registry/compatibility.py` for the full
list of evidence and problem codes.

## Runtime advertisement

The `collect_advertisable_registered_models()` function scans the model-store
and returns only registered models that pass compatibility validation with
`advertisable=true`.

### How it works

1. `WHOOSHD_MODEL_STORE_ROOT` is set to the model-store path
2. `/v1/models` and `/api/tags` collect compatible registered models
3. Each advertisable model is appended to the inventory response
4. Incompatible, indeterminate, or invalid models are skipped
5. Duplicate model IDs that conflict with built-in/static models are skipped

### Configuration

```bash
export WHOOSHD_MODEL_STORE_ROOT=~/whooshd-models
```

When unset, registered models are not advertised and only built-in/static
models appear in inventory.

### Advertisement ≠ execution

```text
registered   -> in registry/models.json
compatible   -> mapped to an adapter kind
advertisable -> appears in /v1/models and /api/tags
runnable     -> loaded by a runtime adapter and proven by generation
```

Advertisement puts the model in the catalog.  It does NOT mean the model
is loaded, warmed, or ready to serve inference.  Those are separate
runtime lifecycle transitions.

## Managed candidate registration

The `register_model_candidate()` function promotes an inspected candidate
into the durable managed model registry.

### Registration flow

1. Load the candidate record from `registry/candidates/<id>.json`
2. Validate the candidate is registrable (status=candidate, format known)
3. Sanitize the model ID (no path traversal, control chars, or empty strings)
4. Copy the artifact into the managed store:
   - MLX text → `models/mlx/<model_id>/`
   - MLX vision → `models/vlm/<model_id>/`
   - GGUF → `models/gguf/<model_id>/<filename>.gguf`
5. Append a `RegisteredModel` entry to `registry/models.json`
6. Return structured registration metadata

### Managed storage

| Format | Modalities | Destination | Adapter |
|--------|-----------|-------------|---------|
| MLX | text only | `models/mlx/<id>/` | `mlx_lm_server` |
| MLX | text + vision | `models/vlm/<id>/` | `mlx_vlm` |
| GGUF | text | `models/gguf/<id>/<file>` | `llama_cpp` |

Copies are managed by Whoosh'd.  Original source files are never modified
or deleted.  External-reference storage is intentionally deferred.

Text-only MLX models always register under `models/mlx/` and map to
`mlx_lm_server`, regardless of processor metadata presence.  Vision-capable
MLX models require explicit multimodal evidence and register under
`models/vlm/`.

Existing registry entries created before this vision-detection correction
may need to be re-registered manually if they were misclassified as
vision-capable.

### Local MLX import workflow

Whoosh'd includes a narrow CLI workflow for consolidating local MLX
cache snapshots into the managed store:

```bash
whoosh import-models
```

By default, the importer bootstraps `~/whooshd-models/` or the path in
`WHOOSHD_MODEL_STORE_ROOT`, scans the common Hugging Face cache roots,
selects active snapshots from `refs/main`, inspects them with
`inspect_model_candidate()`, writes candidate records, registers
compatible models, and then reports the models that are advertisable in
`/v1/models`.

Use `--store-root` to point at a different managed store and
`--scan-root` to add explicit cache roots when the defaults are not
enough.

### Why registration ≠ runtime visibility

A registered model is *known* to Whoosh'd but not yet *advertised* or
*runnable*.  Those transitions require:
- Runtime adapter compatibility validation
- Explicit advertisement into `/v1/models`
- Adapter health confirmation that the model can actually be loaded

Registration is the first durable lifecycle gate, not the last.

### Problem codes

| Code | Meaning |
|------|---------|
| `store_not_bootstrapped` | Store root has no `registry/` dir |
| `candidate_missing` | Candidate record not found |
| `candidate_not_registrable` | Candidate status is not `candidate` |
| `unsupported_format` | Detected format is `unknown` |
| `unsafe_model_id` | Model ID contains path traversal or invalid chars |
| `duplicate_model_id` | A different model already uses this ID |
| `managed_destination_exists` | Destination path already exists |
| `copy_failed` | Filesystem copy operation failed |
| `manifest_unreadable` | `registry/models.json` is corrupt |
| `manifest_schema_unsupported` | Manifest schema_version is not 1 |

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

# Whoosh'd

**Local-first inference broker for Apple Silicon systems.**

Whoosh'd coordinates local model backends such as MLX, llama.cpp, and
related runtimes behind a unified routing surface, with support for
memory-aware orchestration and Codexify-compatible workflows.

Whoosh'd does **not** replace lower-level inference engines. It sits
above them as a lightweight broker for local AI applications that need
routing, task boundaries, model inventory, and model-aware execution.

- **Use with Codexify** as a managed local sidecar, or
- **Use standalone** with any OpenAI-compatible client, or
- **Use with any LLM tool** that speaks `/v1/chat/completions`.

## Core Compatibility

| Surface | Format | Status |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible (streaming + non-streaming) | ✅ |
| `GET /v1/models` | OpenAI-compatible model inventory | ✅ |
| `GET /api/tags` | Ollama-compatible model inventory | ✅ |
| `GET /health` | Process liveness vs runtime state | ✅ |
| `GET /ready` | Warmup / ready / degraded / offline distinction | ✅ |
| Model registry + candidate inspection | Compatibility inspection | ✅ |
| Streaming | SSE with `data:` chunks + `[DONE]` | ✅ |
| Cancellation | Request-scoped cancellation endpoint | ✅ |
| Concurrency | Admission control with structured 429 | ✅ |
| Large context | Configurable max token limits | ✅ |
| Telemetry | Off by default; local-first privacy posture | ✅ |
| Apple Silicon / MLX orientation | Primary backend target | ✅ |
| llama.cpp / GGUF compatibility | Subprocess-supervised adapter | ✅ |

## Architecture

```
Client / Codexify / OpenCode / Xcode
        |
Whoosh'd OpenAI-compatible API
        |
Runtime Router
        +-- llama.cpp runtime for GGUF models
        +-- mlx_lm.server runtime for MLX models
        +-- mlx_lm in-process runtime (legacy)
        +-- stub adapter (testing)
```

## Quick Start

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run with stub adapter (no models needed)
WHOOSHD_ADAPTER=stub python -m uvicorn whooshd.app:app --reload

# Run with MLX-LM Server (subprocess-supervised)
WHOOSHD_MLX_ENABLED=true \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  python -m uvicorn whooshd.app:app --reload

# Run with MLX in-process (legacy, requires mlx-lm)
WHOOSHD_ADAPTER=mlx \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  python -m uvicorn whooshd.app:app --reload

# Run tests (no MLX or model downloads required)
python -m pytest -v

# Smoke-test a running server
python -m whooshd.compat.probe_server --base-url http://localhost:8000
```

## Positioning

Whoosh'd is **not** a replacement for Ollama, llama.cpp, or MLX. It is
a broker that sits above those engines. Use Whoosh'd when you need:

- **Routing** across multiple local runtimes from a single API surface
- **Orchestration** with health, readiness, warmup, and lifecycle control
- **Compatibility** with OpenAI and Codexify conventions without extra tooling
- **Inventory** of available models with compatibility inspection
- **Local-first defaults** with no telemetry, no cloud dependency

## Codexify Integration

See **[docs/codexify-integration.md](docs/codexify-integration.md)** for the full integration guide covering environment configuration, health vs readiness, model lifecycle, streaming expectations, and manual verification.

## Endpoints

| Method | Path                           | Description                               |
|--------|--------------------------------|-------------------------------------------|
| GET    | `/health`                      | Process liveness + runtime state          |
| GET    | `/health/runtime`              | Per-runtime health snapshot               |
| GET    | `/ready`                       | Inference readiness (200/503)             |
| GET    | `/runtime`                     | Full runtime snapshot                     |
| GET    | `/runtime/model`               | Model lifecycle snapshot                  |
| POST   | `/runtime/model/warmup`        | Trigger model warmup on all runtimes      |
| POST   | `/runtime/model/unload`        | Unload models from all runtimes           |
| GET    | `/runtime/requests`            | Request lifecycle list                    |
| POST   | `/runtime/requests/{id}/cancel` | Cancel an active request                  |
| GET    | `/v1/models`                   | OpenAI-compatible model inventory (aggregated) |
| GET    | `/api/tags`                    | Ollama-compatible model inventory (aggregated) |
| POST   | `/v1/chat/completions`         | OpenAI-compatible chat (routed, streaming + non) |
| POST   | `/v1/generate`                 | Codexify-style generation (routed)        |
| GET    | `/models`                      | Internal model registry                   |

Open http://127.0.0.1:8000/docs for the auto-generated Swagger UI.

## Configuration

### General

| Variable                        | Default | Purpose                              |
|---------------------------------|---------|--------------------------------------|
| `WHOOSHD_ADAPTER`               | `stub`  | Backend: `stub`, `mlx`, or `llama_cpp` |
| `WHOOSHD_MODEL_REGISTRY_PATH`   | (none)  | Path to model registry YAML          |

### MLX-LM Server (subprocess-supervised)

| Variable                          | Default                                          | Purpose                         |
|-----------------------------------|--------------------------------------------------|---------------------------------|
| `WHOOSHD_MLX_ENABLED`             | `false`                                          | Enable the MLX-LM Server runtime |
| `WHOOSHD_MLX_MODEL`               | (none)                                           | HF repo or local model path     |
| `WHOOSHD_MLX_HOST`                | `127.0.0.1`                                      | Bind host                       |
| `WHOOSHD_MLX_PORT`                | `8081`                                           | Bind port                       |
| `WHOOSHD_MLX_EXTRA_ARGS`          | (none)                                           | Extra CLI args for mlx_lm.server |
| `WHOOSHD_MLX_STARTUP_TIMEOUT_SECONDS` | `30.0`                                       | Startup timeout                 |
| `WHOOSHD_MLX_HEALTH_TIMEOUT_SECONDS`  | `2.0`                                        | Health probe timeout            |

### MLX in-process (legacy)

| Variable                        | Default                                          | Purpose                       |
|---------------------------------|--------------------------------------------------|-------------------------------|
| `WHOOSHD_MLX_MODEL`             | `mlx-community/Llama-3.2-3B-Instruct-4bit`       | HF repo or local model path   |
| `WHOOSHD_MLX_MAX_TOKENS_DEFAULT`| `256`                                            | Fallback max_tokens           |
| `WHOOSHD_MLX_TRUST_REMOTE_CODE` | `false`                                          | Allow custom model code       |

### llama.cpp (GGUF)

| Variable                              | Default      | Purpose                          |
|---------------------------------------|--------------|----------------------------------|
| `WHOOSHD_LLAMA_CPP_SERVER_URL`        | (none)       | External llama.cpp server URL    |
| `WHOOSHD_LLAMA_CPP_MODEL_PATH`        | (none)       | GGUF model file path             |
| `WHOOSHD_LLAMA_CPP_HOST`              | `127.0.0.1`  | Managed server bind host         |
| `WHOOSHD_LLAMA_CPP_PORT`              | `8080`       | Managed server bind port         |
| `WHOOSHD_LLAMA_CPP_AUTO_START`        | `false`      | Auto-start managed server        |
| `WHOOSHD_LLAMA_CPP_STARTUP_TIMEOUT_SECONDS` | `30.0` | Startup timeout               |
| `WHOOSHD_LLAMA_CPP_HEALTH_TIMEOUT_SECONDS`  | `2.0`  | Health probe timeout            |

## Documentation

- **[Codexify Integration Guide](docs/codexify-integration.md)** — configuration, health vs readiness, streaming
- **[Codexify Live Rehearsal Runbook](docs/codexify-live-rehearsal.md)** — step-by-step integration test
- **[MLX Environment Setup](docs/mlx-environment.md)** — Apple Silicon MLX backend
- **[Model Management](docs/model-management.md)** — downloading, storing, and switching models
- **[Benchmarking](docs/benchmarking.md)** — throughput measurement harness
- **[Release Notes](docs/releases/v0.1-rc.md)** — v0.1 release candidate
- **[Queue Policy](docs/queue-policy.md)** — future queue design (not implemented)
- **[ThreadWake Cache](docs/threadwake/overview.md)** — prompt-prefix reuse optimization (overview, configuration, security, metrics, Codexify integration)

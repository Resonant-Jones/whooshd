# Whoosh'd

<img width="1672" height="941" alt="blue-balloon-whoosh" src="https://github.com/user-attachments/assets/25ed7dae-9d3a-4c8e-9e54-3185ced18831" />

**A local-first inference gateway for Apple Silicon and self-hosted AI
workflows.** It exposes OpenAI-compatible chat and model surfaces while
coordinating local runtimes like MLX and llama.cpp — with explicit
health, readiness, runtime visibility, and safety boundaries.

Whoosh'd is built to be useful as a standalone local inference server,
but especially sharp as the local runtime layer beneath
[Codexify](https://codexify.ai).

**Why not just Ollama or a raw MLX script?**

- **Multi-runtime routing** — run MLX, llama.cpp, and future backends
  behind a single `POST /v1/chat/completions` surface
- **Health and readiness** — distinguish "process alive" from "model
  warm and ready to serve" without guessing
- **Runtime visibility** — `/health/runtime`, `/ready`, `/runtime`,
  structured model inventory
- **ThreadWake cache** — prompt-prefix reuse optimization (metadata
  milestone — analysis and visibility available, KV materialization
  deferred)
- **Codexify-native** — designed as the local inference backend for
  Codexify's local-first posture

Whoosh'd does **not** replace lower-level inference engines. It sits
above them as a lightweight broker.

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
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start Whoosh'd
whoosh -d

# Check status
whoosh status

# Show logs
whoosh logs

# Stop Whoosh'd
whoosh down
```

Alternate entrypoints:

```bash
whooshd-up
whooshd-down
whoosh up
whooshd up
whooshd down
```

One implementation, multiple affordance routes.

These start commands all launch the same server path:

```bash
whoosh -d
whoosh up
whooshd up
whooshd-up
```

These stop commands all stop the same tracked process:

```bash
whoosh down
whooshd down
whooshd-down
```

### One tracked daemon at a time

The Whoosh'd CLI currently tracks one daemon process at a time via:

```text
~/.whooshd/whooshd.pid
~/.whooshd/whooshd.log
```

Custom `--port` values are supported for startup and status probes, but PID and
log state are global, not per-port.

That means:

```bash
whoosh -d --port 8010
whoosh status --port 8010
whoosh down
```

will start and inspect Whoosh'd on port 8010, then stop the globally tracked
Whoosh'd process group.

`whoosh down --port 8010` does not select a separate daemon by port. It stops
the one tracked daemon.

The CLI will not kill unknown processes occupying a port. If a port is already
in use and no tracked Whoosh'd PID exists, inspect it manually:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Developer/debug startup remains available:

```bash
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Useful validation commands:

```bash
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
| GET    | `/health/threadwake`           | ThreadWake cache posture                  |
| GET    | `/runtime/threadwake/analysis` | ThreadWake analysis counts                |
| POST   | `/runtime/threadwake/flush`    | Flush ThreadWake cache entries            |

Open http://127.0.0.1:8000/docs for the auto-generated Swagger UI.

### Runtime Surface Map

| Question | Endpoint |
|---|---|
| Is the server alive? | `GET /health` |
| Can it serve inference now? | `GET /ready` |
| What's the full runtime state? | `GET /runtime` |
| What models are available? | `GET /v1/models` or `GET /api/tags` |
| What's ThreadWake doing? | `GET /health/threadwake` |
| What did ThreadWake find? | `GET /runtime/threadwake/analysis` |

**Rule**: Health tells you if the system is okay. Ready tells you if it can serve. ThreadWake health tells you if the cache subsystem is awake. Analysis tells you what it found.

### Local Sanity Checklist

```bash
# 1. Start server
whoosh -d

# 2. Confirm liveness
curl http://127.0.0.1:8000/health

# 3. Confirm readiness
curl http://127.0.0.1:8000/ready

# 4. List models
curl http://127.0.0.1:8000/v1/models | python3 -m json.tool

# 5. Non-streaming chat
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}]}'

# 6. Streaming chat
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# 7. ThreadWake health (off by default — all zeros)
curl http://127.0.0.1:8000/health/threadwake

# 8. ThreadWake analysis (counts only, safe even when off)
curl http://127.0.0.1:8000/runtime/threadwake/analysis

# Or fetch the same live daemon report from the CLI
python -m whooshd.threadwake.analyze --base-url http://127.0.0.1:8000
```

All commands should succeed with the stub adapter — no models, downloads, or GPU required.

Or run the smoke scripts (server must already be running):

```bash
sh scripts/smoke_stub.sh
sh scripts/smoke_threadwake.sh
sh scripts/smoke_openai_compat.sh
sh scripts/smoke_queue_live.sh
```

**FIFO queue smoke** requires the server to be started with queueing and stub delay enabled:

```bash
WHOOSHD_ADAPTER=stub \
WHOOSHD_ENABLE_QUEUE=true \
WHOOSHD_MAX_ACTIVE_REQUESTS=1 \
WHOOSHD_MAX_QUEUE_DEPTH=8 \
WHOOSHD_QUEUE_TIMEOUT_SECONDS=10 \
WHOOSHD_STUB_RESPONSE_DELAY_SECONDS=2 \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Then run `sh scripts/smoke_queue_live.sh` in another terminal.

Example env files are in `examples/` — copy and adapt for your setup:

| File | Purpose |
|---|---|
| `examples/env.stub` | Stub adapter (no models) |
| `examples/env.codexify` | Codexify local provider connection |
| `examples/env.mlx.example` | MLX in-process adapter |
| `examples/env.llama-cpp.example` | llama.cpp / GGUF adapter |

### Codexify Connection

Point Codexify at Whoosh'd:

```bash
export LOCAL_BASE_URL=http://127.0.0.1:8000/v1
export LOCAL_CHAT_MODEL=stub-model
export LOCAL_API_KEY=local
```

For MLX with a real model:

```bash
export LOCAL_CHAT_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
```

Codexify's `LOCAL_PROVIDER_VENDOR=whooshd` enables ThreadWake segment
metadata emission when `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED=true`.

## ThreadWake Cache

ThreadWake is a **runtime optimization** that reuses pre-computed prompt-prefix
state across chat requests.  It is included in this release as a **metadata
milestone**: the full analysis, policy, manifest, and visibility pipeline is
available, but KV materialization is not enabled.

| Capability | Status |
|---|---|
| Observe-mode prompt analysis | ✅ |
| Candidate telemetry and scoring | ✅ |
| Snapshot policy engine | ✅ |
| Metadata-only analysis loop | ✅ |
| Read-only visibility (`/health/threadwake`, `/runtime/threadwake/analysis`, CLI daemon client) | ✅ |
| Operator runbook and docs index | ✅ |
| KV reuse | ❌ Not enabled |
| Durable KV snapshots | ❌ Deferred |
| Production backend materialization | ❌ No backend supports it |

See **[docs/threadwake/README.md](docs/threadwake/README.md)** for the full documentation index.

## Troubleshooting

### `whooshd --help` still shows Uvicorn help

You may have an old shell function or alias named `whooshd`.
Check:

```bash
type -a whooshd
```

If it says `whooshd` is a shell function from `~/.zshrc`, remove or rename the old function, then reload your shell:

```bash
source ~/.zshrc
hash -r
```

### Port 8000 is already in use

Whoosh'd will not kill unknown processes automatically. If startup reports a port conflict, inspect the listener:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

| Symptom | Check |
|---|---|
| Server not responding | `whoosh status` or `curl http://127.0.0.1:8000/health` |
| `GET /ready` returns 503 | Model may be warming. Check `/runtime/model` for lifecycle state. |
| Warmup hangs | Check model path exists. MLX downloads on first load — wait or check logs. |
| 429 Too Many Requests | Admission control at capacity. Increase `WHOOSHD_MAX_ACTIVE_REQUESTS` or wait. |
| Model listed but not runnable | Check adapter is registered for that runtime kind. See `/health/runtime`. |
| ThreadWake analysis all zeros | Normal when ThreadWake is off or no candidates exist. Enable observe mode first. |
| `pip install` fails | Ensure Python 3.13+ and that optional dependencies (`mlx`, `dev`) match your use case. |

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
- **[Queue Policy](docs/queue-policy.md)** — bounded FIFO request queue (implemented behind `WHOOSHD_ENABLE_QUEUE`, disabled by default)
- **[ThreadWake Cache](docs/threadwake/README.md)** — prompt-prefix reuse optimization (overview, configuration, metrics, security, operator runbook, architecture)

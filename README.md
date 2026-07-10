# Whoosh'd

<img width="1672" height="941" alt="blue-balloon-whoosh" src="https://github.com/user-attachments/assets/25ed7dae-9d3a-4c8e-9e54-3185ced18831" />

**Local-first inference broker for Apple Silicon and self-hosted AI workflows.**

Whoosh'd sits above local runtimes such as MLX-LM Server, MLX-VLM, llama.cpp / GGUF, and the built-in stub adapter. It gives local AI applications a single broker surface for routing, readiness, model inventory, streaming, lifecycle visibility, cancellation, overload behavior, and Codexify-compatible provider boundaries.

Whoosh'd does **not** replace lower-level inference engines. It is the small control plane in front of them.

Use it when you want local inference to behave less like a pile of scripts and more like infrastructure.

## Current status

Whoosh'd is in an active release-candidate stage for the `0.1.x` line.

Current tested posture:

- The default/stub runtime contract is stabilized and full-suite green after the post-expansion runtime/API stabilization pass.
- OpenAI-compatible chat, streaming, generate, health/readiness, model inventory, request lifecycle, Codexify provider compatibility, and the smoke probe path are covered by automated tests.
- Real MLX, GGUF, and VLM runtimes are supported, but must be validated on the target machine with the included runtime validation guides before making deployment claims.
- ThreadWake is present as a metadata, observability, and policy system. Production KV reuse and durable snapshots are not enabled.

This README is intentionally conservative: supported features are listed separately from experimental, deferred, and not-yet-claimed work.

## Supported today

| Area | Status | Notes |
|---|---:|---|
| OpenAI-compatible chat | Supported | `POST /v1/chat/completions`, streaming and non-streaming |
| Codexify-style generate | Supported | `POST /v1/generate`, routed through the same runtime broker |
| Streaming transport | Supported | Server-Sent Events with `data:` chunks and `data: [DONE]` |
| Model inventory | Supported | `GET /v1/models` and `GET /api/tags` |
| Health and readiness | Supported | `GET /health`, `GET /ready`, `GET /health/runtime` |
| Runtime lifecycle | Supported | Runtime snapshots, model warmup, unload, request tracking |
| Request cancellation | Supported | `POST /runtime/requests/{id}/cancel` |
| Admission control | Supported | Structured `429 RUNNER_OVERLOADED` responses |
| Stub adapter | Supported | Default no-model test/runtime path |
| MLX-LM Server lane | Supported | Apple Silicon text runtime. Start `mlx_lm.server` separately or use `scripts/start_mlx_lm_runtime.sh`. |
| MLX in-process lane | Supported, legacy | Still available, but MLX-LM Server is the preferred MLX text path |
| MLX-VLM lane | Supported | Vision-language runtime, requires `mlx-vlm` and validation |
| llama.cpp / GGUF lane | Supported | External or managed GGUF runtime |
| Model registry | Supported | Compatibility-gated model inventory and candidate inspection |
| CLI daemon control | Supported | `whoosh`, `whooshd`, `whooshd-up`, `whooshd-down` |
| Codexify provider boundary | Supported | Stub/default provider compatibility is green; live runtime rehearsal is environment-specific |
| Benchmark harness | Supported | Measures HTTP behavior, latency, TTFT, success/failure/rejection counts |
| ThreadWake observe/metrics | Supported | Metadata-only prompt-prefix analysis and safe health surfaces |
| Bounded FIFO queue | Available behind flag | Disabled by default with `WHOOSHD_ENABLE_QUEUE=false` |

## Experimental, deferred, or not claimed

| Area | Status | Notes |
|---|---:|---|
| Continuous/token-step batching | Not claimed as production-ready | Research and guarded batching work exist, but do not treat this as a production throughput claim |
| Production KV reuse | Not enabled | ThreadWake backend materialization is gated by backend capability |
| Durable KV snapshots | Deferred | Snapshot persistence is explicitly not part of the current supported surface |
| Embeddings endpoint | Not implemented | Future surface |
| Tool/function calling | Not implemented | Future surface |
| Production auth hardening | Not implemented | Local-first development posture only |
| Multi-node routing | Not implemented | Future architecture |
| Live Codexify deployment claim | Not claimed by tests alone | Run the live rehearsal against the target runtime and machine |
| Performance improvement claims | Not automatic | Use the benchmark harness and record hardware/model/runtime details |

## Why not just Ollama or a raw MLX script?

Whoosh'd is for the layer above raw inference:

- **Routing** across local runtime adapters from one API surface
- **Health and readiness** that distinguish process liveness from inference readiness
- **Runtime visibility** through `/health/runtime`, `/runtime`, `/runtime/model`, and `/runtime/requests`
- **Model inventory** through OpenAI-compatible and Ollama-compatible endpoints
- **Admission control** with structured overload responses instead of mysterious 5xx failures
- **Codexify-native operation** as a local provider boundary
- **ThreadWake analysis** for measuring prompt-prefix reuse potential without calling it memory

## Architecture

```text
Client / Codexify / OpenCode / Xcode
        |
Whoosh'd OpenAI-compatible API
        |
Runtime Router
        +-- stub adapter for tests and baseline operation
        +-- mlx_lm.server runtime for MLX text models
        +-- mlx-vlm runtime for vision-language models
        +-- llama.cpp runtime for GGUF models
        +-- mlx_lm in-process runtime (legacy)
```

## Quick start

### Install from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Whoosh'd requires Python `>=3.11`. The default stub path does not require MLX, model downloads, or Apple Silicon.

### Start the local daemon

```bash
# Start Whoosh'd in the background
whoosh -d

# Check status
whoosh status

# Show logs
whoosh logs

# Stop Whoosh'd
whoosh down
```

Equivalent entrypoints are also installed:

```bash
whooshd up
whooshd down
whooshd-up
whooshd-down
```

### One tracked daemon at a time

The CLI currently tracks one daemon process at a time via:

```text
~/.whooshd/whooshd.pid
~/.whooshd/whooshd.log
```

Custom `--port` values are supported for startup and status probes, but PID and log state are global, not per-port.

```bash
whoosh -d --port 8010
whoosh status --port 8010
whoosh down
```

`whoosh down --port 8010` does not select a separate daemon by port. It stops the one tracked daemon.

The CLI will not kill unknown processes occupying a port. If startup reports a port conflict, inspect the listener:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

Developer/debug startup remains available:

```bash
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Local sanity checklist

All of these commands should work with the default stub adapter.

```bash
# 1. Confirm liveness
curl http://127.0.0.1:8000/health

# 2. Confirm readiness
curl http://127.0.0.1:8000/ready

# 3. List models
curl http://127.0.0.1:8000/v1/models | python3 -m json.tool

# 4. Non-streaming chat
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}]}'

# 5. Streaming chat
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# 6. Codexify-style generate
curl -s http://127.0.0.1:8000/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","prompt":"Hello from generate"}'

# 7. ThreadWake health
curl http://127.0.0.1:8000/health/threadwake

# 8. ThreadWake analysis
curl http://127.0.0.1:8000/runtime/threadwake/analysis
```

Run the default test suite without downloading models:

```bash
python -m pytest -v
```

Smoke-test a running server:

```bash
python -m whooshd.compat.probe_server --base-url http://localhost:8000
```

## Runtime setup examples

### Stub mode

```bash
WHOOSHD_ADAPTER=stub \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

### MLX-LM Server

MLX-LM Server is a two-process setup. `WHOOSHD_MLX_ENABLED=true` tells Whoosh'd to connect to an `mlx_lm.server` process at `WHOOSHD_MLX_HOST:WHOOSHD_MLX_PORT`; it does not start that external server by itself in the normal external-server path.

Terminal 1, start `mlx_lm.server`:

```bash
export WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
export WHOOSHD_MLX_HOST=127.0.0.1
export WHOOSHD_MLX_PORT=8081
bash scripts/start_mlx_lm_runtime.sh
```

Terminal 2, start Whoosh'd configured to proxy that runtime:

```bash
WHOOSHD_MLX_ENABLED=true \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
WHOOSHD_MLX_HOST=127.0.0.1 \
WHOOSHD_MLX_PORT=8081 \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Then verify readiness and model inventory:

```bash
curl -i http://127.0.0.1:8000/ready
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
```

If `mlx_lm.server` is not running on the configured host/port, the MLX lane will report offline and requests for that model will fail.

### MLX in-process, legacy

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

### llama.cpp / GGUF

```bash
WHOOSHD_ADAPTER=llama_cpp \
WHOOSHD_LLAMA_CPP_SERVER_URL=http://127.0.0.1:8080 \
WHOOSHD_LLAMA_CPP_MODEL_PATH=/path/to/model.gguf \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

Real runtime claims should include the actual model, machine, runtime command, and validation results. See the runtime validation docs before reporting support for a specific machine/model combination.

## Codexify connection

For stub/provider testing:

```bash
export LLM_PROVIDER=local
export LOCAL_BASE_URL=http://127.0.0.1:8000/v1
export LOCAL_CHAT_MODEL=stub-model
export LOCAL_LLM_MODEL=stub-model
export DEFAULT_LOCAL_MODEL=stub-model
export LOCAL_API_KEY=local
export LOCAL_PROVIDER_VENDOR=whooshd
```

For MLX with a real model:

```bash
export LOCAL_CHAT_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
export LOCAL_LLM_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
export DEFAULT_LOCAL_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
```

Codexify's `LOCAL_PROVIDER_VENDOR=whooshd` enables Whoosh'd-specific behavior such as ThreadWake segment metadata when `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED=true`.

## Endpoint reference

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Process liveness and high-level runtime state |
| GET | `/health/runtime` | Per-runtime health snapshot |
| GET | `/ready` | Inference readiness, returns 200 or 503 |
| GET | `/runtime` | Full runtime snapshot |
| GET | `/runtime/model` | Model lifecycle snapshot |
| POST | `/runtime/model/warmup` | Trigger model warmup on registered runtimes |
| POST | `/runtime/model/unload` | Unload models from registered runtimes |
| GET | `/runtime/requests` | Request lifecycle list |
| POST | `/runtime/requests/{id}/cancel` | Cancel an active request |
| GET | `/v1/models` | OpenAI-compatible model inventory |
| GET | `/api/tags` | Ollama-compatible model inventory |
| POST | `/v1/chat/completions` | OpenAI-compatible chat, streaming and non-streaming |
| POST | `/v1/generate` | Codexify-style generation |
| GET | `/models` | Internal model registry |
| GET | `/health/threadwake` | ThreadWake status and counters |
| GET | `/runtime/threadwake/analysis` | ThreadWake analysis counts |
| POST | `/runtime/threadwake/flush` | Flush ThreadWake cache entries |

Open http://127.0.0.1:8000/docs for the auto-generated Swagger UI.

## ThreadWake Cache

ThreadWake is a **runtime optimization system**, not AI memory.

Current supported posture:

| Capability | Status |
|---|---:|
| Observe-mode prompt analysis | Supported |
| Candidate telemetry and scoring | Supported |
| Snapshot policy engine | Supported |
| Metadata-only analysis loop | Supported |
| Read-only visibility through `/health/threadwake`, `/runtime/threadwake/analysis`, and CLI daemon client | Supported |
| Scope enforcement and safe metadata surfaces | Supported |
| KV reuse | Not enabled |
| Durable KV snapshots | Deferred |
| Production backend materialization | Not claimed |

ThreadWake does not persist conversations, does not store raw prompt content in observability output, and does not imply the model remembers the user.

See **[docs/threadwake/README.md](docs/threadwake/README.md)** for the full ThreadWake documentation index.

## Queue and overload behavior

By default, Whoosh'd rejects over-capacity requests with structured `429 RUNNER_OVERLOADED` responses.

A bounded FIFO queue is available behind an explicit flag:

```bash
WHOOSHD_ENABLE_QUEUE=true
WHOOSHD_MAX_QUEUE_DEPTH=8
WHOOSHD_QUEUE_TIMEOUT_SECONDS=10
```

Queueing is disabled by default. Use it only when you want Whoosh'd to absorb small local bursts instead of making the caller retry.

## Benchmarking and validation

Whoosh'd includes a benchmark harness that measures the server from the outside over HTTP.

It can measure:

- total latency
- time to first visible token for streaming
- success, failure, and rejection counts
- visible output length
- SSE chunk behavior

It does **not** measure model quality and does **not** prove production readiness by itself.

Start here:

- **[Benchmarking Guide](docs/benchmarking.md)**
- **[Benchmark Profiles](docs/benchmark-profiles.md)**
- **[Manual Runtime Validation](docs/manual-runtime-validation.md)**
- **[MLX Environment Setup](docs/mlx-environment.md)**

## Troubleshooting

| Symptom | Check |
|---|---|
| Server not responding | `whoosh status` or `curl http://127.0.0.1:8000/health` |
| `/ready` returns 503 | Model may be warming, unloaded, failed, or degraded. Check `/runtime/model`. |
| MLX lane offline | Make sure `mlx_lm.server` is running on `WHOOSHD_MLX_HOST:WHOOSHD_MLX_PORT`; use `bash scripts/start_mlx_lm_runtime.sh`. |
| Warmup hangs | Check model path exists. MLX may download on first load. |
| 429 Too Many Requests | Admission control is at capacity. Wait, lower concurrency, or enable queueing intentionally. |
| Model listed but not runnable | Check adapter registration and `/health/runtime`. |
| ThreadWake analysis all zeros | Normal when ThreadWake is off or no candidates exist. Enable observe mode first. |
| `pip install` fails | Confirm Python `>=3.11` and install the extras needed for your use case. |
| Port 8000 is already in use | Inspect with `lsof -nP -iTCP:8000 -sTCP:LISTEN`. |
| `whooshd --help` shows Uvicorn help | Check for an old shell function or alias with `type -a whooshd`. |

## Configuration reference

### General

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ADAPTER` | `stub` | Backend selector for stub, MLX in-process, or llama.cpp modes |
| `WHOOSHD_MODEL_REGISTRY_PATH` | none | Path to model registry YAML |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` | Active request admission limit |
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable bounded FIFO queue |

### MLX-LM Server

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_MLX_ENABLED` | `false` | Enable the MLX-LM Server runtime lane in Whoosh'd |
| `WHOOSHD_MLX_MODEL` | none | Hugging Face repo ID or local model path used by both processes |
| `WHOOSHD_MLX_HOST` | `127.0.0.1` | Host where `mlx_lm.server` listens |
| `WHOOSHD_MLX_PORT` | `8081` | Port where `mlx_lm.server` listens |
| `WHOOSHD_MLX_EXTRA_ARGS` | none | Extra CLI args for `mlx_lm.server` |
| `WHOOSHD_MLX_STARTUP_TIMEOUT_SECONDS` | `30.0` | Startup/probe timeout |
| `WHOOSHD_MLX_HEALTH_TIMEOUT_SECONDS` | `2.0` | Health probe timeout |

### MLX in-process, legacy

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_MLX_MODEL` | `mlx-community/Llama-3.2-3B-Instruct-4bit` | Hugging Face repo ID or local model path |
| `WHOOSHD_MLX_MAX_TOKENS_DEFAULT` | `256` | Fallback `max_tokens` |
| `WHOOSHD_MLX_TRUST_REMOTE_CODE` | `false` | Allow custom model code |

### llama.cpp / GGUF

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_LLAMA_CPP_SERVER_URL` | none | External llama.cpp server URL |
| `WHOOSHD_LLAMA_CPP_MODEL_PATH` | none | GGUF model file path |
| `WHOOSHD_LLAMA_CPP_HOST` | `127.0.0.1` | Managed server bind host |
| `WHOOSHD_LLAMA_CPP_PORT` | `8080` | Managed server bind port |
| `WHOOSHD_LLAMA_CPP_AUTO_START` | `false` | Auto-start managed server |
| `WHOOSHD_LLAMA_CPP_STARTUP_TIMEOUT_SECONDS` | `30.0` | Startup timeout |
| `WHOOSHD_LLAMA_CPP_HEALTH_TIMEOUT_SECONDS` | `2.0` | Health probe timeout |

## Documentation

Start with the documentation portal: **[docs/README.md](docs/README.md)**

High-signal links:

- **[Codexify Integration Guide](docs/codexify-integration.md)**
- **[Codexify Live Rehearsal Runbook](docs/codexify-live-rehearsal.md)**
- **[Codexify Runtime Contract Review](docs/codexify-runtime-contract-review.md)**
- **[Manual Runtime Validation](docs/manual-runtime-validation.md)**
- **[MLX Environment Setup](docs/mlx-environment.md)**
- **[Model Management](docs/model-management.md)**
- **[Model Registry](docs/model-registry.md)**
- **[Queue Policy](docs/queue-policy.md)**
- **[Benchmarking](docs/benchmarking.md)**
- **[Benchmark Profiles](docs/benchmark-profiles.md)**
- **[ThreadWake Cache](docs/threadwake/README.md)**
- **[Release Notes](docs/releases/v0.1-rc.md)**
- **[Release-facing Closure](docs/release-notes/whooshd-queue-batching-docs-closure.md)**
- **[Claim Ledger](docs/release-notes/whooshd-queue-batching-docs-claim-ledger.md)**

## Development posture

Whoosh'd is local-first infrastructure for people who want to own their inference boundary.

Supported does not mean magic. Real runtime behavior still depends on:

- machine memory
- Apple Silicon generation
- model size and format
- quantization
- context length
- runtime backend
- warmup state
- concurrency and queue policy

When reporting results, include the model, hardware, runtime command, validation command, and whether the run was cold or warm.

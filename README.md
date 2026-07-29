# Whoosh'd

<img width="1672" height="941" alt="blue-balloon-whoosh" src="https://github.com/user-attachments/assets/25ed7dae-9d3a-4c8e-9e54-3185ced18831" />

**Local-first inference broker for Apple Silicon and self-hosted AI workflows.**

Whoosh'd sits above local runtimes such as MLX-LM Server, MLX-VLM, llama.cpp / GGUF, and the built-in stub adapter. It gives local AI applications one broker surface for routing, readiness, model inventory, streaming, lifecycle visibility, cancellation, overload behavior, bounded control errors, and Codexify-compatible provider boundaries.

Whoosh'd does **not** replace lower-level inference engines. It is the small control plane in front of them.

Use it when you want local inference to behave less like a pile of scripts and more like infrastructure.

## Current status

Whoosh'd is in an active release-candidate stage for the `0.1.x` line.

Current tested posture:

- The default/stub runtime contract is stabilized and full-suite green after the post-expansion runtime/API stabilization pass.
- OpenAI-compatible chat, streaming, generate, health/readiness, model inventory, request lifecycle, Codexify provider compatibility, the bounded control-plane contract, and the smoke probe path are covered by automated tests.
- Real MLX, GGUF, and VLM runtimes are supported, but must be validated on the target machine with the included runtime validation guides before making deployment claims.
- An explicitly configured runtime registry is an operator-owned, fail-closed model allowlist. Unknown and disabled model IDs do not fall through to adapter heuristics.
- ThreadWake is present as a metadata, observability, and policy system. Production KV reuse and durable snapshots are not enabled.

This README is intentionally conservative: supported features are listed separately from experimental, deferred, and not-yet-claimed work.

## Supported today

| Area | Status | Notes |
|---|---:|---|
| OpenAI-compatible chat | Supported | `POST /v1/chat/completions`, streaming and non-streaming |
| Codexify-style generate | Supported | `POST /v1/generate`, routed through the same runtime broker |
| Streaming transport | Supported | Server-Sent Events with `data:` chunks and `data: [DONE]` |
| Model inventory | Supported | `GET /v1/models` and `GET /api/tags` |
| Runtime provenance | Supported | Bounded `whooshd.runtime.v1` evidence on inventory and successful responses |
| Health and readiness | Supported | `GET /health`, `GET /ready`, `GET /health/runtime` |
| Runtime lifecycle | Supported | Runtime snapshots, model warmup, unload, request tracking |
| Request cancellation | Supported | `POST /runtime/requests/{id}/cancel` |
| Control-plane error contract | Supported | Versioned `whooshd.control.v1` envelopes and bounded retry metadata |
| Admission control | Supported | Structured `429 runner_overloaded` responses |
| Stub adapter | Supported | Default no-model test/runtime path |
| MLX-LM Server lane | Supported | Apple Silicon text runtime; start `mlx_lm.server` separately |
| MLX in-process lane | Supported, legacy | Available, but MLX-LM Server is the preferred MLX text path |
| MLX-VLM lane | Supported | Vision-language runtime; requires `mlx-vlm` and validation |
| llama.cpp / GGUF lane | Supported | External or managed GGUF runtime |
| Model registry | Supported | Compatibility-gated inventory and authoritative routing when explicitly configured |
| CLI daemon control | Supported | `whoosh`, `whooshd`, `whooshd-up`, `whooshd-down` |
| Codexify provider boundary | Supported | Stub/default compatibility is green; live runtime rehearsal is environment-specific |
| Benchmark harness | Supported | Measures HTTP behavior, latency, TTFT, and result counts |
| ThreadWake observe/metrics | Supported | Metadata-only prompt-prefix analysis and safe health surfaces |
| Bounded FIFO queue | Available behind flag | Disabled by default with `WHOOSHD_ENABLE_QUEUE=false` |
| launchd runtime bundle | Operator path | Convergent paired-service install for machine-local Whoosh'd + MLX-VLM |

## Experimental, deferred, or not claimed

| Area | Status | Notes |
|---|---:|---|
| Continuous/token-step batching | Not claimed as production-ready | Research and guarded batching work exist; no production throughput claim |
| Production KV reuse | Not enabled | ThreadWake materialization is gated by backend capability |
| Durable KV snapshots | Deferred | Snapshot persistence is outside the current supported surface |
| Embeddings endpoint | Not implemented | Future surface |
| Tool/function calling | Not implemented | Request fields may be retained without implying provider capability |
| Production auth hardening | Not implemented | Local-first development posture only |
| Multi-node routing | Not implemented | Future architecture |
| Live Codexify deployment claim | Not claimed by tests alone | Rehearse against the target runtime and machine |
| Performance improvement claims | Not automatic | Record hardware, model, runtime, and benchmark conditions |
| End-to-end correlation propagation | Not claimed | The July correlation propagation change was reverted |

## Why not just Ollama or a raw MLX script?

Whoosh'd is for the layer above raw inference:

- **Routing** across local runtime adapters from one API surface
- **Operator-owned model policy** through an optional authoritative registry
- **Health and readiness** that distinguish process liveness from inference readiness
- **Runtime visibility** through `/health/runtime`, `/runtime`, `/runtime/model`, and `/runtime/requests`
- **Model inventory** through OpenAI-compatible and Ollama-compatible endpoints
- **Bounded control errors** with stable machine-readable codes instead of message parsing
- **Admission control** with structured overload responses instead of mysterious 5xx failures
- **Codexify-native operation** as a local provider boundary
- **ThreadWake analysis** for measuring prompt-prefix reuse potential without calling it memory

## Architecture

```text
Client / Codexify / OpenCode / Xcode
        |
Whoosh'd OpenAI-compatible API
        |
Ingress contract + backend request policy
        |
Runtime Router
        +-- authoritative registry boundary (when configured)
        +-- stub adapter for tests and baseline operation
        +-- mlx_lm.server runtime for MLX text models
        +-- mlx-vlm runtime for vision-language models
        +-- llama.cpp runtime for GGUF models
        +-- mlx_lm in-process runtime (legacy)
```

The permissive ingress request is filtered into a backend execution request after routing. Internal metadata, ThreadWake controls, reserved orchestration fields, and undeclared inference-shaped extras do not leak into adapter payloads. See [Request and Backend Boundary](docs/request-contract.md).

## Quick start

### Install from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Whoosh'd requires Python `>=3.11`. The default stub path does not require MLX, model downloads, or Apple Silicon.

### Start the daemon

```bash
whoosh -d
whoosh status
whoosh logs
whoosh down
```

Equivalent entrypoints:

```bash
whooshd up
whooshd down
whooshd-up
whooshd-down
```

The CLI tracks one daemon process at a time through `~/.whooshd/whooshd.pid` and `~/.whooshd/whooshd.log`. Custom ports are supported, but PID and log state are global rather than per-port.

Developer startup remains available:

```bash
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Local sanity checklist

```bash
# Liveness and readiness
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready

# Model inventory
curl http://127.0.0.1:8000/v1/models | python3 -m json.tool

# Non-streaming chat
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}]}'

# Streaming chat
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}],"stream":true}'

# Codexify-style generate
curl -s http://127.0.0.1:8000/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"model_id":"stub-model","prompt":"Hello from generate"}'

# ThreadWake health and analysis
curl http://127.0.0.1:8000/health/threadwake
curl http://127.0.0.1:8000/runtime/threadwake/analysis
```

Run tests without downloading models:

```bash
python -m pytest -v
```

Smoke-test a running server:

```bash
python -m whooshd.compat.probe_server --base-url http://localhost:8000
```

## Runtime setup

### Stub

```bash
WHOOSHD_ADAPTER=stub \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

### MLX-LM Server

MLX-LM Server is a two-process setup. `WHOOSHD_MLX_ENABLED=true` connects Whoosh'd to an independently running `mlx_lm.server` process.

```bash
# Terminal 1
export WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
export WHOOSHD_MLX_HOST=127.0.0.1
export WHOOSHD_MLX_PORT=8081
bash scripts/start_mlx_lm_runtime.sh

# Terminal 2
WHOOSHD_MLX_ENABLED=true \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
WHOOSHD_MLX_HOST=127.0.0.1 \
WHOOSHD_MLX_PORT=8081 \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

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

Real runtime claims should include the actual model, machine, runtime command, validation command, and whether the run was cold or warm.

## Authoritative model registry

When `WHOOSHD_MODEL_REGISTRY_PATH` is explicitly set, the registry becomes the operator-owned routing boundary:

- enabled entries may resolve to their declared runtime adapter
- unknown model IDs are rejected before heuristic fallback
- disabled model IDs are rejected before execution
- an unavailable configured registry fails closed

Installations without an explicitly configured registry retain compatibility fallback behavior.

A machine-specific single-model guest profile is included at `configs/models.friends-family-guest.yaml`. It is an example deployment policy, not a portable default; its local model path must be adapted to the target machine.

## Codexify connection

```bash
export LLM_PROVIDER=local
export LOCAL_BASE_URL=http://127.0.0.1:8000/v1
export LOCAL_CHAT_MODEL=stub-model
export LOCAL_LLM_MODEL=stub-model
export DEFAULT_LOCAL_MODEL=stub-model
export LOCAL_API_KEY=local
export LOCAL_PROVIDER_VENDOR=whooshd
```

For a real model, replace the three model variables with the exact model ID advertised by Whoosh'd.

### Control-plane contract

Clients opting into the bounded machine-readable contract send:

```http
X-Whooshd-Contract-Version: whooshd.control.v1
```

Whoosh'd-owned responses advertise the same version. Canonical errors include stable codes, HTTP status, retryability, optional bounded retry timing, request identity when available, category, and operational details that exclude prompts, generated text, credentials, raw upstream bodies, and private paths.

A missing request header preserves the legacy-compatible path. An explicit unsupported version returns `contract_version_unsupported` with HTTP 400. Streaming failures after visible output begins produce a canonical SSE error event and do not fabricate a successful `[DONE]` sentinel.

See [Control-Plane Contract v1](docs/control-plane-v1.md).

### Runtime provenance

Whoosh'd can attach bounded runtime evidence using schema `whooshd.runtime.v1`:

- `/v1/models` includes provenance in model metadata
- native inventory exposes `runtime_provenance`
- non-streaming chat and generate responses carry an additive provenance field
- streaming chat keeps the established SSE body and sends provenance in `X-Whooshd-Runtime-Provenance`

Provenance identifies the code path used. It contains no prompts, completions, media, URLs, filesystem paths, process identifiers, environment values, or credentials, and it is not proof that a live runtime or model is currently available.

## Endpoint reference

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Process liveness and high-level runtime state |
| GET | `/health/runtime` | Per-runtime health snapshot |
| GET | `/ready` | Inference readiness, returns 200 or 503 |
| GET | `/runtime` | Full runtime snapshot |
| GET | `/runtime/model` | Model lifecycle snapshot |
| POST | `/runtime/model/warmup` | Trigger model warmup |
| POST | `/runtime/model/unload` | Unload models |
| GET | `/runtime/requests` | Request lifecycle list |
| POST | `/runtime/requests/{id}/cancel` | Cancel an active request |
| GET | `/v1/models` | OpenAI-compatible model inventory |
| GET | `/api/tags` | Ollama-compatible model inventory |
| POST | `/v1/chat/completions` | OpenAI-compatible chat |
| POST | `/v1/generate` | Codexify-style generation |
| GET | `/models` | Internal model registry |
| GET | `/health/threadwake` | ThreadWake status and counters |
| GET | `/runtime/threadwake/analysis` | ThreadWake analysis counts |
| POST | `/runtime/threadwake/flush` | Flush ThreadWake entries |

Open `http://127.0.0.1:8000/docs` for Swagger UI.

## ThreadWake Cache

ThreadWake is a **runtime optimization system**, not AI memory.

| Capability | Status |
|---|---:|
| Observe-mode prompt analysis | Supported |
| Candidate telemetry and scoring | Supported |
| Snapshot policy engine | Supported |
| Metadata-only analysis loop | Supported |
| Scope enforcement and safe metadata surfaces | Supported |
| KV reuse | Not enabled |
| Durable KV snapshots | Deferred |
| Production backend materialization | Not claimed |

ThreadWake does not persist conversations, does not store raw prompt content in observability output, and does not imply the model remembers the user.

See [ThreadWake documentation](docs/threadwake/README.md).

## Queue and overload behavior

By default, Whoosh'd rejects over-capacity requests with structured `429 runner_overloaded` responses.

```bash
WHOOSHD_ENABLE_QUEUE=true
WHOOSHD_MAX_QUEUE_DEPTH=8
WHOOSHD_QUEUE_TIMEOUT_SECONDS=10
```

Queueing is disabled by default. Enabling it does not enable batching by itself; the live queued HTTP path remains FIFO unless separately gated experimental batching is enabled.

## Persistent local operation with launchd

The repository includes a machine-local launchd bundle for a paired Whoosh'd proxy and MLX-VLM upstream. The renderer and installer require an explicit Python interpreter, validate imports before privileged mutation, validate generated plists, use an exclusion lock, classify exact service targets, and converge from registered, absent, or mixed two-service state.

This is an operator path, not a portable zero-configuration installer. Registered services do not prove listener containment, model readiness, generation success, or restart stability. Run the separate live containment and smoke checks.

See [Whoosh'd Local launchd Runtime](docs/ops/whooshd-launchd-local-runtime.md).

## Benchmarking and validation

The benchmark harness measures HTTP behavior, total latency, time to first visible token, success/failure/rejection counts, visible output length, and SSE chunk behavior. It does not measure model quality and does not prove production readiness by itself.

Start here:

- [Benchmarking Guide](docs/benchmarking.md)
- [Benchmark Profiles](docs/benchmark-profiles.md)
- [Manual Runtime Validation](docs/manual-runtime-validation.md)
- [MLX Environment Setup](docs/mlx-environment.md)

## Troubleshooting

| Symptom | Check |
|---|---|
| Server not responding | `whoosh status` or `curl http://127.0.0.1:8000/health` |
| `/ready` returns 503 | Check `/runtime/model`; the model may be warming, unloaded, failed, or degraded |
| MLX lane offline | Confirm `mlx_lm.server` is running at the configured host and port |
| 429 Too Many Requests | Lower concurrency, retry/back off, or enable queueing intentionally |
| Configured model rejected | Confirm the exact model ID exists and is enabled in the active registry |
| Registry cannot load | Explicit registries fail closed; fix the path or contents |
| Model listed but not runnable | Check `/health/runtime` and run a real generation smoke |
| Contract version rejected | Send `whooshd.control.v1` or omit the header for legacy compatibility |
| ThreadWake analysis is zero | Enable observe mode and generate eligible traffic |
| Port 8000 is occupied | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |

## Configuration reference

### General

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ADAPTER` | `stub` | Select stub, MLX in-process, or llama.cpp mode |
| `WHOOSHD_MODEL_REGISTRY_PATH` | none | Explicit authoritative runtime registry YAML |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` | Active request admission limit |
| `WHOOSHD_ENABLE_QUEUE` | `false` | Enable bounded FIFO queue |

### MLX-LM Server

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_MLX_ENABLED` | `false` | Enable the MLX-LM Server lane |
| `WHOOSHD_MLX_MODEL` | none | Hugging Face repo ID or local path |
| `WHOOSHD_MLX_HOST` | `127.0.0.1` | Upstream host |
| `WHOOSHD_MLX_PORT` | `8081` | Upstream port |
| `WHOOSHD_MLX_EXTRA_ARGS` | none | Extra server arguments |
| `WHOOSHD_MLX_STARTUP_TIMEOUT_SECONDS` | `30.0` | Startup/probe timeout |
| `WHOOSHD_MLX_HEALTH_TIMEOUT_SECONDS` | `2.0` | Health timeout |

### MLX in-process, legacy

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_MLX_MODEL` | `mlx-community/Llama-3.2-3B-Instruct-4bit` | Model ID or local path |
| `WHOOSHD_MLX_MAX_TOKENS_DEFAULT` | `256` | Fallback token limit |
| `WHOOSHD_MLX_TRUST_REMOTE_CODE` | `false` | Allow custom model code |

### llama.cpp / GGUF

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_LLAMA_CPP_SERVER_URL` | none | External server URL |
| `WHOOSHD_LLAMA_CPP_MODEL_PATH` | none | GGUF path |
| `WHOOSHD_LLAMA_CPP_HOST` | `127.0.0.1` | Managed server host |
| `WHOOSHD_LLAMA_CPP_PORT` | `8080` | Managed server port |
| `WHOOSHD_LLAMA_CPP_AUTO_START` | `false` | Auto-start managed server |
| `WHOOSHD_LLAMA_CPP_STARTUP_TIMEOUT_SECONDS` | `30.0` | Startup timeout |
| `WHOOSHD_LLAMA_CPP_HEALTH_TIMEOUT_SECONDS` | `2.0` | Health timeout |

## Documentation

Start with [docs/README.md](docs/README.md).

High-signal links:

- [Architecture Overview](docs/architecture.md)
- [Operator Guide](docs/operator-guide.md)
- [API Reference](docs/api-reference.md)
- [Request and Backend Boundary](docs/request-contract.md)
- [Control-Plane Contract v1](docs/control-plane-v1.md)
- [Logging Safety Contract](docs/security/whooshd-logging-safety.md)
- [Codexify Integration Guide](docs/codexify-integration.md)
- [Codexify Live Rehearsal](docs/codexify-live-rehearsal.md)
- [Runtime Contract Review](docs/codexify-runtime-contract-review.md)
- [Manual Runtime Validation](docs/manual-runtime-validation.md)
- [Model Management](docs/model-management.md)
- [Model Registry](docs/model-registry.md)
- [Queue and Admission](docs/queue-and-admission.md)
- [ThreadWake Cache](docs/threadwake/README.md)
- [Local launchd Runtime](docs/ops/whooshd-launchd-local-runtime.md)
- [Release Notes](docs/releases/v0.1-rc.md)
- [Claim Ledger](docs/release-notes/whooshd-queue-batching-docs-claim-ledger.md)

## Development posture

Whoosh'd is local-first infrastructure for people who want to own their inference boundary.

Supported does not mean magic. Real runtime behavior still depends on machine memory, Apple Silicon generation, model size and format, quantization, context length, runtime backend, warmup state, authoritative registry policy, concurrency, and queue policy.

When reporting results, include the model, hardware, runtime command, validation command, and whether the run was cold or warm.

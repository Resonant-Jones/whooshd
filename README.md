# Whoosh'd

Memory-aware local inference broker for Apple Silicon.

## Quick Start

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run with stub adapter (no models needed)
WHOOSHD_ADAPTER=stub python -m uvicorn whooshd.app:app --reload

# Run with MLX (requires mlx-lm and a converted model)
WHOOSHD_ADAPTER=mlx \
  WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
  python -m uvicorn whooshd.app:app --reload

# Run tests (no MLX or model downloads required)
python -m pytest -v

# Smoke-test a running server
python -m whooshd.compat.probe_server --base-url http://localhost:8000
```

## Codexify Integration

See **[docs/codexify-integration.md](docs/codexify-integration.md)** for the full integration guide covering environment configuration, health vs readiness, model lifecycle, streaming expectations, and manual verification.

## Endpoints

| Method | Path                           | Description                               |
|--------|--------------------------------|-------------------------------------------|
| GET    | `/health`                      | Process liveness + runtime state          |
| GET    | `/ready`                       | Inference readiness (200/503)             |
| GET    | `/runtime`                     | Full runtime snapshot                     |
| GET    | `/runtime/model`               | Model lifecycle snapshot                  |
| POST   | `/runtime/model/warmup`        | Trigger model warmup                      |
| POST   | `/runtime/model/unload`        | Unload model from memory                  |
| GET    | `/runtime/requests`            | Request lifecycle list                    |
| POST   | `/runtime/requests/{id}/cancel` | Cancel an active request                  |
| GET    | `/v1/models`                   | OpenAI-compatible model inventory         |
| GET    | `/api/tags`                    | Ollama-compatible model inventory         |
| POST   | `/v1/chat/completions`         | OpenAI-compatible chat (streaming + non)  |
| POST   | `/v1/generate`                 | Codexify-style generation                 |
| GET    | `/models`                      | Internal model registry                   |

Open http://127.0.0.1:8000/docs for the auto-generated Swagger UI.

## Configuration

| Variable                        | Default                                          | Purpose                       |
|---------------------------------|--------------------------------------------------|-------------------------------|
| `WHOOSHD_ADAPTER`               | `stub`                                           | Backend: `stub` or `mlx`      |
| `WHOOSHD_MLX_MODEL`             | `mlx-community/Llama-3.2-3B-Instruct-4bit`       | HF repo or local model path   |
| `WHOOSHD_MLX_MAX_TOKENS_DEFAULT`| `256`                                            | Fallback max_tokens           |
| `WHOOSHD_MLX_TRUST_REMOTE_CODE` | `false`                                          | Allow custom model code       |

## Documentation

- **[Codexify Integration Guide](docs/codexify-integration.md)** — configuration, health vs readiness, streaming
- **[Codexify Live Rehearsal Runbook](docs/codexify-live-rehearsal.md)** — step-by-step integration test
- **[MLX Environment Setup](docs/mlx-environment.md)** — Apple Silicon MLX backend
- **[Benchmarking](docs/benchmarking.md)** — throughput measurement harness
- **[Release Notes](docs/releases/v0.1-rc.md)** — v0.1 release candidate
- **[Queue Policy](docs/queue-policy.md)** — future queue design (not implemented)

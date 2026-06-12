# MLX Runtime Environment

How to set up and validate a real `mlx-lm` backend for Whoosh'd.

---

## Purpose

Whoosh'd supports an optional MLX backend for Apple Silicon machines.
This document covers environment setup, model selection, and manual
validation.  The stub backend is always available and requires no
additional dependencies.

---

## Supported Target

- **Hardware:** Apple Silicon Mac (M1, M2, M3, M4 series)
- **OS:** macOS 14+ (Sonoma or later recommended)
- **Python:** 3.11+

MLX uses Apple's Metal framework and unified memory.  It does not run
on Intel Macs or non-macOS platforms.

---

## Python Environment

Whoosh'd default tests and stub mode work without `mlx-lm`.

```bash
# Create venv and install Whoosh'd (stub only)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Installing mlx-lm (Optional)

`mlx-lm` is an optional dependency.  It is only needed when running
Whoosh'd with `WHOOSHD_ADAPTER=mlx`.

### Manual install

```bash
pip install mlx-lm
```

### Using optional extras (if configured)

```bash
pip install -e ".[mlx]"
```

---

## Choosing a Model

Use a small, known-good instruct model for validation:

```text
mlx-community/Llama-3.2-3B-Instruct-4bit
```

This is the Whoosh'd default for `WHOOSHD_MLX_MODEL`.

If that model is unavailable or too large for the machine:

- Try `mlx-community/Qwen2.5-1.5B-Instruct-4bit` (smaller, faster to load)
- Use any MLX-format model from HuggingFace
- Convert models using `mlx_lm.convert`

For downloading models locally and switching between them, see
**[Model Management](model-management.md)**.

Rules:

- **Do not silently substitute models in reports.**
- **Document the actual model used.**
- **Prefer small, fast-to-load models for validation.**
- **Model choice for smoke tests is not product positioning.**

---

## Starting Whoosh'd with MLX

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

The first run may download the model from HuggingFace (cached to
`~/.cache/huggingface/`).  Subsequent runs will load from cache.

---

## Warmup Flow

### Check initial state

```bash
curl -s http://localhost:8000/health
curl -i http://localhost:8000/ready
curl -s http://localhost:8000/runtime/model
```

Expected before warmup:

```
/health → 200 (process alive)
/ready  → 503 (model unloaded)
/runtime/model → lifecycle_state: unloaded
```

### Trigger warmup

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
```

During warmup, `/runtime/model` will show:

```
lifecycle_state: warming
```

After successful warmup:

```bash
curl -i http://localhost:8000/ready
curl -s http://localhost:8000/runtime/model
```

Expected:

```
/ready → 200
/runtime/model → lifecycle_state: ready
```

If warmup fails, check `/runtime/model` for error details.

---

## Non-Streaming Smoke

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "messages": [{"role":"user","content":"Say hello from Whooshd in one sentence."}],
    "stream": false,
    "max_tokens": 64
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'status=ok, chars={len(d[\"choices\"][0][\"message\"][\"content\"])}, finish={d[\"choices\"][0][\"finish_reason\"]}')"
```

Expected:

```
OpenAI-compatible response with non-empty content and finish_reason
```

Do not paste generated text into committed reports unless it is generic.

---

## Streaming Smoke

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "messages": [{"role":"user","content":"Say hello from Whooshd in one sentence."}],
    "stream": true,
    "max_tokens": 64
  }'
```

Expected:

```
data: {...}  (SSE chunks)
data: [DONE]
```

Verify after stream completes:

```bash
curl -s http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'active_jobs={d[\"active_jobs\"]}')"
```

Expected: `active_jobs=0`

---

## Benchmark Smoke (MLX)

Only after warmup succeeds:

```bash
python -m whooshd.bench.runner \
  --base-url http://localhost:8000 \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --concurrency 1 \
  --requests 2 \
  --stream \
  --max-tokens 64 \
  --json
```

Keep this tiny.  This validates the measurement path, not throughput.

---

## Troubleshooting

| Symptom | Likely cause | Next check |
|---|---|---|
| `ModuleNotFoundError: mlx_lm` | Optional dependency missing | `pip install mlx-lm` |
| Model warmup fails | Model unavailable or incompatible | Try smaller known-good model |
| `/ready` stays 503 | Lifecycle failed/unloaded | Inspect `/runtime/model` |
| Stream starts but no content | Adapter/generator issue | Run non-streaming smoke first |
| `active_jobs` stuck after stream | Lifecycle cleanup bug | Inspect `/runtime/requests` |
| First run very slow | Model download in progress | Check `~/.cache/huggingface/` |
| OOM or memory pressure | Model too large for available RAM | Use smaller model or 4-bit quantization |

---

## Known Limitations

- MLX requires Apple Silicon and macOS 14+.
- `mlx-lm` is not a hard dependency of Whoosh'd.
- **Automated tests never download models. No model downloads occur during normal tests.**
- The stub backend is always available and is the default.
- First model load may download several GB of weights.
- Character throughput is not token throughput.
- MLX benchmark results are hardware-specific.

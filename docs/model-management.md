# Model Management

How to acquire, store, and switch between MLX models in Whoosh'd.

---

## Overview

Whoosh'd delegates model loading to `mlx-lm`, which accepts two kinds of
model references through the `WHOOSHD_MLX_MODEL` environment variable:

| Kind | Example | Behaviour |
|---|---|---|
| **HF repo ID** | `mlx-community/Llama-3.2-3B-Instruct-4bit` | Downloaded from HuggingFace on first load, cached to `~/.cache/huggingface/` |
| **Local path** | `models/my-model` or `/absolute/path` | Loaded directly from disk; no network required |

Either form is passed straight to `mlx_lm.load()` — Whoosh'd does not
wrap or transform the path.

---

## Model storage convention

Whoosh'd expects models to live under a `models/` directory in the
project root.  This directory is gitignored by default so weights are
never committed.

```
Whoosh'd/
├── models/                          ← gitignored
│   ├── llama-3.2-3b-4bit/         ← one directory per model
│   │   ├── config.json
│   │   ├── model.safetensors
│   │   └── tokenizer.json
│   └── gemma-4-2b-3bit-mixed/
│       ├── config.json
│       ├── model.safetensors
│       └── tokenizer.json
```

You are free to use any directory layout — `WHOOSHD_MLX_MODEL` just
needs to point at the right one.

---

## Downloading a model from HuggingFace

Use the `hf` CLI (part of `huggingface_hub`):

```bash
# Install the tooling (if not already present)
pip install "huggingface_hub[hf_xet]"

# Download a model to models/
hf download \
  --local-dir models/my-model-name \
  username/repo-name
```

For example:

```bash
hf download \
  --local-dir models/gemma-4-E2B-it-ultra-uncensored-heretic-MLX-3bit-mixed_3_6 \
  zecanard/gemma-4-E2B-it-ultra-uncensored-heretic-MLX-3bit-mixed_3_6
```

**Note:** The legacy `huggingface-cli` command is deprecated.  Use `hf`
instead (it ships with `huggingface_hub` ≥ 1.0).

For faster downloads set a HuggingFace token:

```bash
hf auth login
# or
export HF_TOKEN=hf_...
```

Unauthenticated downloads work but are rate-limited.

---

## Switching models

Change `WHOOSHD_MLX_MODEL` to point at the new model, then restart
Whoosh'd:

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=models/my-other-model \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

If the previous model is still loaded in memory you may want to unload
it first:

```bash
curl -X POST http://localhost:8000/runtime/model/unload
```

Then trigger warmup for the new model:

```bash
curl -X POST http://localhost:8000/runtime/model/warmup
```

---

## Automatic metadata detection

When `WHOOSHD_MLX_MODEL` points to a **local directory**, Whoosh'd reads
`config.json` from that directory at startup to populate the model
inventory (`/models`, `/v1/models`, `/runtime/model`).

### Detected fields

| Field | Source | Example |
|---|---|---|
| `context_window` | `text_config.max_position_embeddings` in `config.json` | `131072` |
| `quantization` | `quantization.bits` and `quantization.mode` in `config.json` | `"4bit-affine"` |
| `memory_class` | Heuristic from total `.safetensors` file size | `"small"` (< 8 GB) |

If `config.json` can't be read (HF repo ID, missing file, bad JSON) the
fields fall back to defaults.

### Override with environment variables

You can bypass auto-detection entirely:

```bash
export WHOOSHD_MLX_CONTEXT_WINDOW=131072
export WHOOSHD_MLX_QUANTIZATION="3bit-mixed_3_6"
```

These take priority over `config.json` values.  Set `WHOOSHD_MLX_CONTEXT_WINDOW=0`
to use auto-detection.

---

## Verification

After starting Whoosh'd with a local model, verify the metadata was
picked up:

```bash
# Check the model inventory
curl -s http://localhost:8000/models | python3 -m json.tool

# Check the model lifecycle snapshot
curl -s http://localhost:8000/runtime/model | python3 -m json.tool
```

Look for `context_window`, `quantization`, and `memory_class` in the
output.  They should match the actual model, not the hardcoded defaults.

---

## Environment variable reference

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_ADAPTER` | `stub` | `"stub"` or `"mlx"` |
| `WHOOSHD_MLX_MODEL` | `mlx-community/Llama-3.2-3B-Instruct-4bit` | HF repo ID or local path |
| `WHOOSHD_MLX_CONTEXT_WINDOW` | `0` (auto-detect) | Override context window in tokens |
| `WHOOSHD_MLX_QUANTIZATION` | `""` (auto-detect) | Override quantization label |
| `WHOOSHD_MLX_TRUST_REMOTE_CODE` | `false` | Allow custom model code |
| `WHOOSHD_MLX_MAX_TOKENS_DEFAULT` | `256` | Fallback `max_tokens` |

---

## Troubleshooting

| Symptom | Likely cause | Next check |
|---|---|---|
| Model metadata shows defaults (32768, None) | `WHOOSHD_MLX_MODEL` is an HF repo ID, not a local path | Download model locally first |
| `config.json` not found | Path is wrong or directory doesn't exist | `ls models/my-model/config.json` |
| `quantization` shows `None` | `config.json` has no `quantization` field | Set `WHOOSHD_MLX_QUANTIZATION` manually |
| Warmup fails after switching models | Old model still in memory | `POST /runtime/model/unload` first |
| `hf download` hangs | Rate-limited (no token) | `hf auth login` or set `HF_TOKEN` |

# Gemma E2B Live Runtime Proof — 2026-06-16

**Date**: 2026-06-16
**Repo**: Whoosh'd `/Users/resonant_jones/Keep/Resonant_Constructs/Whoosh'd`
**Branch**: `main`
**HEAD commit**: `0b4205b feat(registry): advertise Gemma E2B MLX model`
**Phase 37 commit**: `0b4205b` (present)

## Task Classification

This document captures Phase 37B: Gemma MLX runtime execution proof and Codexify
availability proof.

## Result Summary

**Gemma E2B live execution through Whoosh'd: NOT PROVEN.**

Root cause: MLX framework version incompatibility. The Gemma 4 E2B architecture
(`gemma4` model type) is not supported by mlx-lm 0.31.3, which is the latest
available version as of this date. Model weights contain attention-normalization
parameters (`k_norm.weight`, `k_proj.biases`, `k_proj.scales`) that the installed
mlx-lm version does not recognize, causing a `ValueError: Received 140 parameters
not in model` during first-inference weight loading.

## Timeout Classification

| Aspect | Finding |
|--------|---------|
| mlx_lm.server not running | No — server starts and serves `/v1/models` |
| mlx_lm.server running wrong model | Partially — port 8081 is Llama-configured but lists Gemma in inventory |
| Cold-load longer than 45s | No — first inference fails immediately with weight mismatch |
| Whoosh'd adapter timeout too short | No — failure is immediate model-load error, not timeout |
| Whoosh'd health marked ready too early | Yes — see Readiness Gap below |
| Gemma model load failed | **YES — mlx-lm 0.31.3 does not support gemma4 architecture** |
| Gemma completed after client timeout | No — load fails entirely |

## Commands Run

### Upstream mlx_lm.server (Gemma-specific attempt)

```bash
cd Whoosh'd
source .venv/bin/activate
python -m mlx_lm server --model mlx-community/gemma-4-e2b-it-4bit \
  --host 127.0.0.1 --port 8089
```

### Upstream /v1/models result

```json
{
  "data": [
    {"id": "mlx-community/Qwen2-VL-2B-Instruct-4bit"},
    {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit"},
    {"id": "mlx-community/gemma-4-e2b-it-4bit"}
  ]
}
```

Server returns 200 OK. Model IDs listed but the server cannot serve Gemma completions.

### Upstream direct completion result

```
Exception in thread Thread-1 (_generate):
ValueError: Received 140 parameters not in model:
language_model.model.layers.15.self_attn.k_norm.weight, ...
```

Model load fails with weight architecture mismatch. The HTTP endpoint is reachable
but POST to `/v1/chat/completions` for Gemma hangs indefinitely (no response
after 180s) because the model loading thread crashed silently.

### Existing upstream (port 8081)

The existing mlx_lm.server on port 8081 runs with `--model
mlx-community/Llama-3.2-3B-Instruct-4bit`. It lists Gemma in `/v1/models` but
returns 404 for Gemma completions. This server was used as the Whoosh'd upstream
for the Gemma-configured proof instance.

### Whoosh'd proof instance

```bash
WHOOSHD_MLX_ENABLED=true \
WHOOSHD_MLX_MODEL="mlx-community/gemma-4-e2b-it-4bit" \
WHOOSHD_MLX_HOST=127.0.0.1 \
WHOOSHD_MLX_PORT=8081 \
WHOOSHD_MLX_VLM_ENABLED=true \
WHOOSHD_MLX_VLM_MODEL="mlx-community/Qwen2-VL-2B-Instruct-4bit" \
WHOOSHD_MLX_VLM_HOST=127.0.0.1 \
WHOOSHD_MLX_VLM_PORT=8082 \
WHOOSHD_LLAMA_CPP_SERVER_URL=http://127.0.0.1:9090 \
WHOOSHD_LLAMA_CPP_MODEL_PATH="models/gguf/qwen2.5-0.5b-instruct-q4_k_m.gguf" \
WHOOSHD_MODEL_REGISTRY_PATH=configs/models.validated.yaml \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 18000
```

### Whoosh'd /health/runtime result (with new structured evidence fields)

```json
{
  "mlx_lm_server": {
    "kind": "mlx_lm_server",
    "enabled": true,
    "state": "ready",
    "active_model": null,
    "configured_model": "mlx-community/gemma-4-e2b-it-4bit",
    "detail": "mlx_lm.server /v1/models returned 200.",
    "configured_model_available": true,
    "upstream_reachable": true,
    "upstream_models": [
      "mlx-community/Qwen2-VL-2B-Instruct-4bit",
      "mlx-community/gemma-4-e2b-it-4bit",
      "mlx-community/Llama-3.2-3B-Instruct-4bit"
    ],
    "readiness_reason": "configured_model_advertised"
  }
}
```

### Whoosh'd /v1/models result

```json
{
  "data": [
    {"id": "qwen2.5-0.5b-gguf"},
    {"id": "gemma-4-e2b-mlx"},
    {"id": "qwen2-vl-2b-mlx"}
  ]
}
```

`gemma-4-e2b-mlx` is advertised. `llama-3.2-3b-mlx` is hidden because
`WHOOSHD_MLX_MODEL=mlx-community/gemma-4-e2b-it-4bit` is the active text model.

### Whoosh'd /api/tags result

Includes `gemma-4-e2b-mlx` with engine=mlx_lm, format=mlx, modalities=[text].

### Whoosh'd Gemma non-streaming smoke

```
Status: 404
{"code":"MODEL_NOT_FOUND","message":"Model not found on upstream server: mlx-community/gemma-4-e2b-it-4bit"}
```

The adapter correctly forwards to port 8081, which returns 404 because its
loaded model is Llama, not Gemma.

### Whoosh'd Gemma streaming smoke

```
Status: 502
{"code":"INTERNAL","message":"Unexpected transport error communicating with http://127.0.0.1:8081: Upstream returned 404 for streaming request"}
```

### Existing Whoosh'd Llama smoke (port 8000)

```
Status: 200 — "Hello!"
```

Llama route confirmed working on the production instance (port 8000,
Llama-configured).

## Readiness Semantics Change

Added structured evidence fields to `RuntimeHealth` model:

| Field | Type | Description |
|-------|------|-------------|
| `configured_model_available` | bool? | Whether the configured model appears in upstream `/v1/models` data |
| `upstream_reachable` | bool? | Whether the upstream server responded to the health probe |
| `upstream_models` | list[str]? | Model IDs reported by upstream `/v1/models` |
| `readiness_reason` | str? | Why the runtime is classified at its current state |

The MLX-LM Server adapter now:
1. Parses the upstream `/v1/models` data array to extract model IDs
2. Checks whether the configured model path appears in that array
3. Reports readiness reasons: `configured_model_advertised`, `configured_model_not_advertised`, `upstream_reachable_model_unknown`, `model_warming`, `upstream_health_probe_failed`, `upstream_unreachable`, or `runtime_disabled`

The `readiness_reason` field makes the basis for "ready" classification
transparent. `configured_model_advertised` means the probe was limited to
`/v1/models` success — it does NOT mean the model has been proven by
completion.

### Remaining Readiness Gap

The `readiness_reason: "configured_model_advertised"` is honest about its
limits but does not detect model-load failures. When the upstream
`mlx_lm.server` lists a model in `/v1/models` that cannot actually be
served (due to architecture mismatch), Whoosh'd still reports `state: ready`.

A deeper readiness probe would require:
1. A minimal completion attempt during health check (heavy/invasive)
2. Upstream support for model-load-status reporting in the API
3. Detection of completion failures and state transition to `degraded`

This is a known limitation documented here. The upstream `mlx_lm.server`
should ideally distinguish "server running" from "model loadable" in its
health response.

## Regression: Llama / Qwen / GGUF

| Lane | Status | Evidence |
|------|--------|----------|
| Llama MLX text (port 8000) | ✅ preserved | Live completion returns "Hello!" on port 8000 |
| Gemma MLX text (registry filtered) | ✅ preserved | Only gemma-4-e2b-mlx advertised when Gemma active |
| Qwen2-VL vision | ✅ preserved | 162 vision tests pass; qwen2-vl-2b-mlx in inventory |
| Qwen GGUF | ✅ preserved | qwen2.5-0.5b-gguf in inventory; llama_cpp adapter registered |
| Inactive MLX text alias not fallen through | ✅ preserved | llama-3.2-3b-mlx hidden when Gemma configured |

## Codexify Compatibility

Not run. Prerequisites not met: Gemma live execution was not proven through
Whoosh'd. The primary success condition (Gemma completion success) was not
achieved.

Codexify compatibility is deferred to a future phase after mlx-lm supports
the gemma4 architecture or an alternative Gemma model that is compatible
with the current mlx-lm version.

## Tests Run

| Test Group | Count | Result |
|------------|-------|--------|
| Full suite (`pytest -q`) | 946 | ✅ all passed |
| `test_runtime_inventory.py` | ✅ | all passed |
| `test_multi_runtime_routing.py` | ✅ | all passed |
| `test_model_lifecycle.py` | ✅ | all passed |
| `test_chat_completions_contract.py` | ✅ | all passed |
| `test_registry.py` | ✅ | all passed |
| `test_codexify_provider_compat.py` | ✅ | all passed |
| `test_codexify_provider_smoke.py` | ✅ | all passed |
| `test_codexify_stream_compat.py` | ✅ | all passed |
| `test_llama_cpp_adapter.py` | ✅ | all passed |
| `test_vision_routing.py` | ✅ | all passed |

## Files Changed

- `whooshd/contracts.py` — Added `configured_model_available`, `upstream_reachable`, `upstream_models`, `readiness_reason` fields to `RuntimeHealth`
- `whooshd/adapters/mlx_lm_server.py` — Updated `_MlxLmServerHealthStatus` with upstream model tracking; updated `_probe_server()` to extract model IDs from upstream `/v1/models`; updated `health()` to populate new structured evidence fields
- `docs/gemma-e2b-live-runtime-proof-2026-06-16.md` — This proof artifact

## Known Limitations

1. **Gemma E2B cannot execute**: The `gemma-4-e2b-it-4bit` model uses the `gemma4` architecture, not yet supported by mlx-lm 0.31.3 (latest). Model load fails with weight parameter mismatch.
2. **Readiness probe is `/v1/models` only**: `configured_model_advertised` means the model appears in upstream inventory. It does NOT mean the model can serve completions. The upstream mlx_lm.server does not expose model-load health status.
3. **Upstream mlx_lm.server lists models it cannot serve**: The mlx_lm.server's `/v1/models` includes models that are present in the HuggingFace cache but not loadable by the running server instance. This causes a false-positive in Whoosh'd's health check.
4. **Codexify compatibility not proven**: Blocked by item 1.

## Recommended Next Phase

1. **Wait for mlx-lm Gemma 4 support**: Monitor mlx-lm releases for `gemma4` architecture support, then re-run this proof.
2. **Alternative: Use a compatible Gemma model**: If another Gemma model quantized for MLX is compatible with mlx-lm 0.31.3 (e.g., `gemma-2-2b-it`), test that as an alternative.
3. **Upstream readiness signal**: Consider proposing or implementing a model-load-status endpoint in `mlx_lm.server` that reports whether the configured model has been successfully loaded and can accept inference.
4. **Codexify compatibility proof**: After item 1 or 2 is resolved, run the Codexify compatibility proof using the `docs/codexify-drop-in-smoke-test.md` guide.

## Status Table

| Area | Status | Evidence |
|------|--------|----------|
| Repo boundary | Whoosh'd | HEAD 0b4205b |
| Clean worktree start | pass | git status clean |
| Upstream Gemma MLX server | fail | mlx-lm 0.31.3 gemma4 incompatibility |
| Upstream direct completion | fail | ValueError: 140 parameters not in model |
| Whoosh'd Gemma inventory | pass | gemma-4-e2b-mlx in /v1/models and /api/tags |
| Whoosh'd Gemma health | pass (with caveat) | ready; readiness_reason=configured_model_advertised |
| Whoosh'd Gemma non-streaming smoke | fail | 404 from upstream |
| Whoosh'd Gemma streaming smoke | fail | 502 from upstream |
| Readiness classification | configured_model_advertised | Honest about evidence source |
| Llama route preserved | pass | Live completion OK on port 8000 |
| Vision route preserved | pass | 162 vision tests pass |
| GGUF route preserved | pass | qwen2.5-0.5b-gguf in inventory |
| Codexify availability flip | not run | Blocked by Gemma execution failure |
| No cloud fallback | verified (via config posture) | local-only Whoosh'd |
| Tests | pass | 946 passed |
| Docs proof | committed | This artifact |

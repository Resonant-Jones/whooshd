# Guarded MLX Adapter-Batch Runtime Validation

Operator-safe proof that guarded MLX adapter batching can be started,
smoked, inspected, and rolled back. No production crown, no speed claims.

## Scope

Validates: disabled default, one-flag disabled, two-flag enabled two-request
MLX text-only non-streaming path, response-shape parity, no metadata leaks,
queue drain, rollback by removing flags.

Does NOT validate: token-step continuous batching, shared decode-loop scheduling,
performance improvement, production readiness, streaming, VLM, tools, llama.cpp.

## Preconditions

- Apple/MLX machine
- MLX backend configured
- Text-only MLX model available (e.g. `mlx-community/Llama-3.2-3B-Instruct-4bit`)
- Whoosh'd installed in editable/dev mode

## Flags

Enable:
```bash
export WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=true
export WHOOSHD_GUARDED_ADAPTER_BATCHING_MIN_GROUP_SIZE=2
export WHOOSHD_GUARDED_ADAPTER_BATCHING_MAX_GROUP_SIZE=2
```

Disable (default):
```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```

## Validation Steps

### 1. Disabled default

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```
Start server, send one request. Verify: existing path works, guarded path not active.

### 2. One-flag disabled

Global only or MLX only. Verify: guarded path remains disabled.

### 3. Two-flag enabled

Set both flags. Send two compatible non-streaming text-only requests.
Verify: both return 200, OpenAI-compatible shape, no metadata leaks, queue drains.

### 4. Response-shape inspection

Verify: `id`, `object`, `model`, `choices[0].message.content` present.
Verify: `slot_id`, `tombstone`, `sampling_signature`, `guarded_adapter` absent.

### 5. Metadata leak check

Inspect response bodies and internal reports for: `SECRET_PROMPT`,
`token_ids`, `slot_id`, `tombstone`, `traceback`, `cache_ref`.

### 6. Rollback

```bash
unset WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED
unset WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED
```
Restart server. Verify: existing path works, guarded path disabled.

## Results Template

See `docs/runtime-validation-results-guarded-adapter-batching-template.md`.

## Verdict Values

`passed` | `failed` | `inconclusive`

## Important

This validation does not claim production readiness or performance improvement.
It validates guarded adapter-batch behavior only — not true token-step
continuous batching.

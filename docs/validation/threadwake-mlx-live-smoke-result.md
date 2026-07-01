# Live MLX ThreadWake Smoke Result

**Date:** 2026-07-01
**Machine:** Apple M4, macOS, arm64
**Model:** `mlx-community/Llama-3.2-3B-Instruct-4bit`

## Environment

```bash
WHOOSHD_ADAPTER=mlx
WHOOSHD_THREADWAKE_ENABLED=true
WHOOSHD_THREADWAKE_MODE=ephemeral
WHOOSHD_THREADWAKE_MLX_TOKENIZER_ENABLED=true
WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit
```

## Smoke Output

```
Whoosh'd Live MLX ThreadWake Smoke
  base:  http://127.0.0.1:8000
  model: mlx-community/Llama-3.2-3B-Instruct-4bit

  PASS  /health reachable
  PASS  /ready reachable
  PASS  /v1/models contains selected model
  PASS  ThreadWake enabled
  PASS  ThreadWake mode is ephemeral
  PASS  MLX KV capability is experimental
  PASS  first request returned valid chat completion
  PASS  second request returned valid chat completion
  PASS  first request produced non-empty content
  PASS  second request produced non-empty content
  PASS  ThreadWake entry count increased after requests
  PASS  ThreadWake cache activity observed (entries created)
  PASS  no request failure observed
  PASS  health surfaces do not leak prompt text
  PASS  health surfaces do not leak opaque cache internals
  PASS  health surfaces do not leak generated text

---
Passed: 16  Failed: 0
```

## Result

**16/16 PASS.** The experimental MLX prompt-cache path works against a real local MLX model. ThreadWake reports `experimental` capability, chat requests complete successfully, cache entries are created, and no prompt/token/cache internals leak through public runtime surfaces.

## Limitations

- This validates behavior, not performance.
- Experimental mode only — production acceleration not claimed.
- Clone, disk persistence, concurrency safety, scheduler, and batching remain blocked.
- The `mlx_kv_feasibility` probe source detection was fixed during validation (resolved incorrect module import path for `inspect.getsource`).

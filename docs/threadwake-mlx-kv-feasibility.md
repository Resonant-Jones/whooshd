# ThreadWake MLX KV Feasibility

Result: **feasible** through the generic KV protocol.

## What was probed

MLX-LM 0.31.3 provides a prompt-cache API:

- `make_prompt_cache(model) -> List[Any]` — creates a per-model KV cache
- `stream_generate(..., prompt_cache=cache)` — uses the cache as a pre-filled KV prefix
- `trim_prompt_cache(cache, num_tokens)` — trims a cache
- `save_prompt_cache / load_prompt_cache` — disk persistence

## Protocol mapping

The MLX-LM prompt-cache API is **token-based**: the model receives `model(token_ids, cache=prompt_cache)`. This maps cleanly to ThreadWake's generic KV protocol:

| ThreadWake | MLX-LM |
|---|---|
| `prefill_to_kv(token_ids)` | `model(stable_token_ids, cache=prompt_cache)` → populate cache |
| `generate_from_kv(handle, dynamic_ids)` | `stream_generate(..., token_ids=dynamic_ids, prompt_cache=cache)` |
| `release_kv(handle)` | Drop cache references |

## What's implemented

The `MLXKVBackendAdapter` implements experimental `prefill_to_kv` and `generate_from_kv` when `WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true` and MLX-LM is installed.

The implementation:
- Creates a prompt cache via `make_prompt_cache(model)`
- Populates it by running the model on the stable prefix tokens
- Generates from the cache by passing it to `stream_generate` with dynamic tail tokens

## What's NOT implemented

- **Clone**: Cache cloning requires deep-copying per-layer MLX state. Not yet proven safe.
- **Disk persistence**: `save_prompt_cache` / `load_prompt_cache` exist but are not wired.
- **Concurrency safety**: Multiple requests sharing the same cache object have not been tested.

## Blockers before production

1. Cache creation incurs inference latency (running the model on prefix tokens).
2. The cache is tied to the same model object — cross-request sharing requires the model to stay loaded.
3. Multi-request concurrency safety is untested.
4. Cache cloning requires deep-copying per-layer MLX state.

## Current capability

By default: `UNSUPPORTED`.

With `WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true` and MLX-LM installed: `EXPERIMENTAL`.

No cloneable, resumable, or serializable claims are made.

# MLX Experimental Live Batching

MLX live batching behind explicit experimental gates. Disabled by default.

## Status

Experimental. MLX live batching is an opt-in local runtime feature that uses
Whoosh'd's hardened batch handoff path and MLX-LM `batch_generate` to execute
compatible queued non-streaming text-only requests as one backend batch.

## Gating

```
WHOOSHD_BATCH_EXECUTION_ENABLED=true
+ WHOOSHD_MLX_BATCH_EXECUTION_ENABLED=true
+ batch_generate import available
+ compatible batch group found
→ MLX reports experimental
```

Without the MLX-specific flag:
```
MLX supports_chat_batching() = "unsupported"
```

## Prompt rendering

The batch path uses the same shared MLX prompt renderer as single requests.
No separate transcript format. Tokenizer fidelity is preserved.

## Limitations

- No prompt-cache handoff in live path yet
- No streaming batching
- No vision batching
- No continuous batching
- Sampling settings must be compatible (conservative: identical temperature/top_p/max_tokens)
- No performance claims

## Manual smoke

```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_ENABLE_QUEUE=true \
WHOOSHD_MAX_ACTIVE_REQUESTS=1 \
WHOOSHD_BATCH_ANALYSIS_ENABLED=true \
WHOOSHD_BATCH_EXECUTION_ENABLED=true \
WHOOSHD_MLX_BATCH_EXECUTION_ENABLED=true \
MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
scripts/smoke_mlx_live_batching.sh
```

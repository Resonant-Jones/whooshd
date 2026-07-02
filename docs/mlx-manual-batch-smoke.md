# MLX Manual Batch Smoke

Manual smoke test proving MLX-LM `batch_generate` returns one output per
prompt, preserves order, and supports prompt-cache handoff.

**This is a manual test only. It does not enable live-path MLX batching.**

## Prerequisites

- Apple Silicon (M-series)
- macOS 14+
- `mlx-lm` installed
- A local MLX model available

## Run

```bash
MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
BATCH_SIZE=2 \
MAX_TOKENS=16 \
scripts/smoke_mlx_batch_manual.sh
```

Or directly:

```bash
python scripts/smoke_mlx_batch_manual.py \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --batch-size 2 --max-tokens 16
```

To see generated output (debug only):

```bash
python scripts/smoke_mlx_batch_manual.py \
  --model mlx-community/Llama-3.2-3B-Instruct-4bit \
  --show-output
```

## What passing means

- `batch_generate` returns N outputs for N prompts
- Response order is preserved (index → response mapping)
- Prompt cache handoff works (first pass stores caches, second pass reuses them)
- No prompts, token IDs, or cache internals are leaked in the report

## What passing does NOT mean

- MLX live-path batching is NOT enabled
- MLX adapter capability is NOT changed
- Production batching is NOT ready
- No performance or benchmark claim is made

## Report fields

| Field | Meaning |
|---|---|
| `status` | `passed`, `failed`, or `inconclusive` |
| `first_pass.response_count_verified` | N outputs for N prompts |
| `first_pass.response_order_verified` | Each index has non-empty output |
| `first_pass.prompt_cache_returned` | Cache object returned by API |
| `second_pass.response_count_verified` | Handoff pass returned correct count |
| `live_path_enabled` | Always `false` |
| `generated_text_included` | `false` unless `--show-output` |

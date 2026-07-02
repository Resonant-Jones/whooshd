#!/bin/sh
set -euo pipefail
MODEL="${MODEL:-mlx-community/Llama-3.2-3B-Instruct-4bit}"
BATCH_SIZE="${BATCH_SIZE:-2}"
MAX_TOKENS="${MAX_TOKENS:-16}"
SECOND_PASS="${SECOND_PASS:-true}"
SHOW_OUTPUT="${SHOW_OUTPUT:-false}"
ARGS="--model $MODEL --batch-size $BATCH_SIZE --max-tokens $MAX_TOKENS --json"
if [ "$SECOND_PASS" = "false" ]; then ARGS="$ARGS --no-second-pass"; fi
if [ "$SHOW_OUTPUT" = "true" ]; then ARGS="$ARGS --show-output"; fi
python3 scripts/smoke_mlx_batch_manual.py $ARGS

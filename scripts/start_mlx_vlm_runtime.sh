#!/usr/bin/env bash
# ── Whoosh'd MLX-VLM Runtime Startup Helper ──────────────────────────────
#
# Starts an mlx-vlm server instance for Whoosh'd to proxy.
# MLX-VLM provides vision-language model inference.
#
# Usage:
#   bash scripts/start_mlx_vlm_runtime.sh
#
# Required:
#   WHOOSHD_MLX_VLM_MODEL — HF repo ID or local path
#
# Optional:
#   WHOOSHD_MLX_VLM_HOST         — bind host (default: 127.0.0.1)
#   WHOOSHD_MLX_VLM_PORT         — bind port (default: 8082)
#   WHOOSHD_MLX_VLM_EXTRA_ARGS   — additional CLI args

set -euo pipefail

HOST="${WHOOSHD_MLX_VLM_HOST:-127.0.0.1}"
PORT="${WHOOSHD_MLX_VLM_PORT:-8082}"
EXTRA_ARGS="${WHOOSHD_MLX_VLM_EXTRA_ARGS:-}"

if [ -z "${WHOOSHD_MLX_VLM_MODEL:-}" ]; then
    echo "ERROR: WHOOSHD_MLX_VLM_MODEL is not set."
    echo "Example: export WHOOSHD_MLX_VLM_MODEL=mlx-community/Qwen2-VL-2B-Instruct-4bit"
    exit 1
fi

MODEL="$WHOOSHD_MLX_VLM_MODEL"

if ! python -c "import mlx_vlm" 2>/dev/null; then
    echo "ERROR: mlx-vlm is not installed. Install with: pip install mlx-vlm"
    exit 1
fi

CMD=(python -m mlx_vlm server --model "$MODEL" --host "$HOST" --port "$PORT")
if [ -n "$EXTRA_ARGS" ]; then
    for arg in $EXTRA_ARGS; do CMD+=("$arg"); done
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Starting MLX-VLM runtime for Whoosh'd"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Model  : $MODEL"
echo "Host   : $HOST"
echo "Port   : $PORT"
echo "Command: ${CMD[*]}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exec "${CMD[@]}"

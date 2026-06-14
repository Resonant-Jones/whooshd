#!/usr/bin/env bash
# ── Whoosh'd MLX-LM Server Runtime Startup Helper ──────────────────────────
#
# Starts an mlx_lm.server instance for Whoosh'd to proxy.
# Reads configuration from environment variables with safe defaults.
#
# Usage:
#   source scripts/start_mlx_lm_runtime.sh
#   OR
#   bash scripts/start_mlx_lm_runtime.sh
#
# Required environment variables:
#   WHOOSHD_MLX_MODEL — HF repo ID or local path to the MLX model
#
# Optional:
#   WHOOSHD_MLX_HOST        — bind host (default: 127.0.0.1)
#   WHOOSHD_MLX_PORT        — bind port (default: 8081)
#   WHOOSHD_MLX_EXTRA_ARGS  — additional CLI args passed through to mlx_lm.server

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────

HOST="${WHOOSHD_MLX_HOST:-127.0.0.1}"
PORT="${WHOOSHD_MLX_PORT:-8081}"
EXTRA_ARGS="${WHOOSHD_MLX_EXTRA_ARGS:-}"

# ── Validate required vars ────────────────────────────────────────────────

if [ -z "${WHOOSHD_MLX_MODEL:-}" ]; then
    echo "ERROR: WHOOSHD_MLX_MODEL is not set."
    echo "Set it to the HF repo ID or local path of the MLX model."
    echo "Example: export WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit"
    exit 1
fi

MODEL="$WHOOSHD_MLX_MODEL"

# ── Check mlx-lm is installed ─────────────────────────────────────────────

if ! python -c "import mlx_lm" 2>/dev/null; then
    echo "ERROR: mlx-lm is not installed."
    echo "Install it with: pip install mlx-lm"
    exit 1
fi

# ── Build command ─────────────────────────────────────────────────────────
# mlx-lm >= 0.31 uses "python -m mlx_lm server" (space, not dot).
# The older "python -m mlx_lm.server" form is deprecated.

CMD=(python -m mlx_lm server --model "$MODEL" --host "$HOST" --port "$PORT")

if [ -n "$EXTRA_ARGS" ]; then
    # Word-split extra args (safe: user controls these explicitly).
    # shellcheck disable=SC2086
    for arg in $EXTRA_ARGS; do
        CMD+=("$arg")
    done
fi

# ── Print and run ─────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Starting MLX-LM Server runtime for Whoosh'd"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Model  : $MODEL"
echo "Host   : $HOST"
echo "Port   : $PORT"
echo "Command: ${CMD[*]}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exec "${CMD[@]}"

#!/usr/bin/env bash
# ── Whoosh'd llama.cpp Runtime Startup Helper ─────────────────────────────
#
# Starts a llama-server instance for Whoosh'd to proxy.
# Reads configuration from environment variables with safe defaults.
#
# Usage:
#   source scripts/start_llama_cpp_runtime.sh
#   OR
#   bash scripts/start_llama_cpp_runtime.sh
#
# Required environment variables:
#   WHOOSHD_LLAMA_CPP_MODEL_PATH  — path to GGUF model file
#   WHOOSHD_LLAMA_CPP_BINARY_PATH — path to llama-server binary
#
# Optional:
#   WHOOSHD_LLAMA_CPP_HOST        — bind host (default: 127.0.0.1)
#   WHOOSHD_LLAMA_CPP_PORT        — bind port (default: 8080)
#   WHOOSHD_LLAMA_CPP_CTX_SIZE    — context window size override
#   WHOOSHD_LLAMA_CPP_PARALLEL    — parallel slots (default: 1)
#   WHOOSHD_LLAMA_CPP_EXTRA_ARGS  — additional CLI args passed through

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────

HOST="${WHOOSHD_LLAMA_CPP_HOST:-127.0.0.1}"
PORT="${WHOOSHD_LLAMA_CPP_PORT:-8080}"
CTX_SIZE="${WHOOSHD_LLAMA_CPP_CTX_SIZE:-}"
PARALLEL="${WHOOSHD_LLAMA_CPP_PARALLEL:-}"
EXTRA_ARGS="${WHOOSHD_LLAMA_CPP_EXTRA_ARGS:-}"

# ── Validate required vars ────────────────────────────────────────────────

if [ -z "${WHOOSHD_LLAMA_CPP_BINARY_PATH:-}" ]; then
    echo "ERROR: WHOOSHD_LLAMA_CPP_BINARY_PATH is not set."
    echo "Set it to the path of the llama-server binary."
    echo "Example: export WHOOSHD_LLAMA_CPP_BINARY_PATH=/usr/local/bin/llama-server"
    exit 1
fi

if [ -z "${WHOOSHD_LLAMA_CPP_MODEL_PATH:-}" ]; then
    echo "ERROR: WHOOSHD_LLAMA_CPP_MODEL_PATH is not set."
    echo "Set it to the path of the GGUF model file."
    echo "Example: export WHOOSHD_LLAMA_CPP_MODEL_PATH=/models/qwen3-coder-30b/q4_k_m.gguf"
    exit 1
fi

BINARY="$WHOOSHD_LLAMA_CPP_BINARY_PATH"
MODEL="$WHOOSHD_LLAMA_CPP_MODEL_PATH"

# ── Validate binary exists ────────────────────────────────────────────────

if [ ! -f "$BINARY" ]; then
    echo "ERROR: llama-server binary not found at $BINARY"
    exit 1
fi

# ── Validate model exists ─────────────────────────────────────────────────

if [ ! -f "$MODEL" ]; then
    echo "ERROR: GGUF model file not found at $MODEL"
    exit 1
fi

# ── Build command ─────────────────────────────────────────────────────────

CMD=("$BINARY" "--model" "$MODEL" "--host" "$HOST" "--port" "$PORT")

if [ -n "$CTX_SIZE" ] && [ "$CTX_SIZE" -gt 0 ] 2>/dev/null; then
    CMD+=("--ctx-size" "$CTX_SIZE")
fi

if [ -n "$PARALLEL" ] && [ "$PARALLEL" -gt 0 ] 2>/dev/null; then
    CMD+=("--parallel" "$PARALLEL")
fi

if [ -n "$EXTRA_ARGS" ]; then
    # Word-split extra args (safe: user controls these explicitly).
    # shellcheck disable=SC2086
    for arg in $EXTRA_ARGS; do
        CMD+=("$arg")
    done
fi

# ── Print and run ─────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Starting llama.cpp runtime for Whoosh'd"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Binary : $BINARY"
echo "Model  : $MODEL"
echo "Host   : $HOST"
echo "Port   : $PORT"
echo "Command: ${CMD[*]}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exec "${CMD[@]}"

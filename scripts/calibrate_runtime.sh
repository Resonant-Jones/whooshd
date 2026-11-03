#!/usr/bin/env bash
# Real-runtime calibration runner (roadmap P0).
#
# Runs outside the agent sandbox so the harness can reach Metal when it is
# executing locally.  Writes a JSON report and an optional markdown summary.
# It never mutates runtime state: it only issues requests and writes files.
#
# Usage:
#   scripts/calibrate_runtime.sh [--output PATH] [--markdown PATH] [extra args...]
#
# Environment:
#   WHOOSHD_BASE_URL         broker base URL        (default http://127.0.0.1:8000)
#   WHOOSHD_UPSTREAM_URL     upstream runtime URL   (default http://127.0.0.1:8082)
#   WHOOSHD_UPSTREAM_MODEL   upstream model id
#   WHOOSHD_CALIBRATION_MODEL  broker model id      (default local-chat)
#   WHOOSHD_CALIBRATION_BANDS  concurrency bands     (default "1 2 3 4")
#   WHOOSHD_CALIBRATION_REQUESTS requests per band   (default 8)
#   WHOOSHD_VENV             python interpreter     (default .venv311/bin/python)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV="${WHOOSHD_VENV:-$REPO_ROOT/.venv311/bin/python}"
BASE_URL="${WHOOSHD_BASE_URL:-http://127.0.0.1:8000}"
UPSTREAM_URL="${WHOOSHD_UPSTREAM_URL:-http://127.0.0.1:8082}"
MODEL="${WHOOSHD_CALIBRATION_MODEL:-local-chat}"
BANDS="${WHOOSHD_CALIBRATION_BANDS:-1 2 3 4}"
REQUESTS="${WHOOSHD_CALIBRATION_REQUESTS:-8}"
STAMP="${WHOOSHD_CALIBRATION_STAMP:-$(date -u +%Y-%m-%d)}"
OUT_DIR="${WHOOSHD_CALIBRATION_OUT_DIR:-$REPO_ROOT/docs/validation}"

OUTPUT="${OUT_DIR}/${STAMP}-runtime-calibration.json"
MARKDOWN="${OUT_DIR}/${STAMP}-runtime-calibration.md"

if [ ! -x "$VENV" ]; then
  echo "calibration aborted: interpreter not found at $VENV" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"

# Upstream model id is optional; when unset the harness skips upstream probes
# and reports cache counters as unavailable rather than fabricating them.
ARGS=(
  --base-url "$BASE_URL"
  --model "$MODEL"
  --bands $BANDS
  --requests "$REQUESTS"
  --output "$OUTPUT"
  --markdown "$MARKDOWN"
)
if [ -n "${WHOOSHD_UPSTREAM_URL:-}" ]; then
  ARGS+=(--upstream-url "$UPSTREAM_URL")
fi
if [ -n "${WHOOSHD_UPSTREAM_MODEL:-}" ]; then
  ARGS+=(--upstream-model "$WHOOSHD_UPSTREAM_MODEL")
fi

echo "whooshd runtime calibration" >&2
echo "  base    : $BASE_URL" >&2
echo "  upstream: ${WHOOSHD_UPSTREAM_URL:-<none>}" >&2
echo "  model   : $MODEL" >&2
echo "  bands   : $BANDS" >&2
echo "  output  : $OUTPUT" >&2

exec "$VENV" -m whooshd.bench.runtime_calibration "${ARGS[@]}" "$@"

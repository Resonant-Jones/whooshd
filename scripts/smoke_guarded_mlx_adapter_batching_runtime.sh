#!/bin/sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
"$DIR/.venv/bin/python" "$DIR/scripts/smoke_guarded_mlx_adapter_batching_runtime.py"

#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-dry-run}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/.local/launchd}"
WHOOSHD_LABEL="${WHOOSHD_LABEL:-com.resonant.whooshd}"
MLX_VLM_LABEL="${MLX_VLM_LABEL:-com.resonant.mlx-vlm-gemma12b}"
WHOOSHD_PLIST="$OUTPUT_DIR/$WHOOSHD_LABEL.plist"
MLX_VLM_PLIST="$OUTPUT_DIR/$MLX_VLM_LABEL.plist"
SYSTEM_DIR="/Library/LaunchDaemons"
WHOOSHD_TARGET="$SYSTEM_DIR/$WHOOSHD_LABEL.plist"
MLX_VLM_TARGET="$SYSTEM_DIR/$MLX_VLM_LABEL.plist"
WHOOSHD_BACKUP="$SYSTEM_DIR/$WHOOSHD_LABEL.plist.bak"

usage() {
  cat <<'USAGE'
Usage:
  bash ops/launchd/install_local_launchd.sh
  bash ops/launchd/install_local_launchd.sh dry-run
  bash ops/launchd/install_local_launchd.sh install

Defaults to dry-run. The script never stores credentials and expects sudo to
be provided by the operator for install mode.
USAGE
}

if [[ "$MODE" != "dry-run" && "$MODE" != "install" ]]; then
  usage
  exit 2
fi

if [[ ! -f "$WHOOSHD_PLIST" || ! -f "$MLX_VLM_PLIST" ]]; then
  echo "Rendered plists not found in $OUTPUT_DIR"
  echo "Run: python3 ops/launchd/render_launchd_plists.py --output-dir \"$OUTPUT_DIR\" --dry-run"
  exit 1
fi

plutil -lint "$WHOOSHD_PLIST"
plutil -lint "$MLX_VLM_PLIST"

echo "Validated:"
echo "  $WHOOSHD_PLIST"
echo "  $MLX_VLM_PLIST"

if [[ "$MODE" == "dry-run" ]]; then
  echo
  echo "Dry run only. Install commands:"
  echo "  sudo cp \"$WHOOSHD_TARGET\" \"$WHOOSHD_BACKUP\" 2>/dev/null || true"
  echo "  sudo cp \"$WHOOSHD_PLIST\" \"$WHOOSHD_TARGET\""
  echo "  sudo cp \"$MLX_VLM_PLIST\" \"$MLX_VLM_TARGET\""
  echo "  sudo chown root:wheel \"$WHOOSHD_TARGET\" \"$MLX_VLM_TARGET\""
  echo "  sudo chmod 644 \"$WHOOSHD_TARGET\" \"$MLX_VLM_TARGET\""
  echo "  sudo launchctl bootout system/$WHOOSHD_LABEL 2>/dev/null || true"
  echo "  sudo launchctl bootout system/$MLX_VLM_LABEL 2>/dev/null || true"
  echo "  sudo launchctl bootstrap system \"$WHOOSHD_TARGET\""
  echo "  sudo launchctl bootstrap system \"$MLX_VLM_TARGET\""
  echo "  sudo launchctl kickstart -k system/$WHOOSHD_LABEL"
  echo "  sudo launchctl kickstart -k system/$MLX_VLM_LABEL"
  exit 0
fi

sudo cp "$WHOOSHD_TARGET" "$WHOOSHD_BACKUP" 2>/dev/null || true
sudo cp "$WHOOSHD_PLIST" "$WHOOSHD_TARGET"
sudo cp "$MLX_VLM_PLIST" "$MLX_VLM_TARGET"
sudo chown root:wheel "$WHOOSHD_TARGET" "$MLX_VLM_TARGET"
sudo chmod 644 "$WHOOSHD_TARGET" "$MLX_VLM_TARGET"
sudo launchctl bootout system/"$WHOOSHD_LABEL" 2>/dev/null || true
sudo launchctl bootout system/"$MLX_VLM_LABEL" 2>/dev/null || true
sudo launchctl bootstrap system "$WHOOSHD_TARGET"
sudo launchctl bootstrap system "$MLX_VLM_TARGET"
sudo launchctl kickstart -k system/"$WHOOSHD_LABEL"
sudo launchctl kickstart -k system/"$MLX_VLM_LABEL"

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
SYSTEM_DIR="${SYSTEM_DIR:-/Library/LaunchDaemons}"
WHOOSHD_TARGET="$SYSTEM_DIR/$WHOOSHD_LABEL.plist"
MLX_VLM_TARGET="$SYSTEM_DIR/$MLX_VLM_LABEL.plist"
WHOOSHD_BACKUP="$SYSTEM_DIR/$WHOOSHD_LABEL.plist.bak"

PLUTIL_BIN="${PLUTIL_BIN:-plutil}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SUDO_BIN="${SUDO_BIN:-sudo}"
LAUNCHCTL_BIN="${LAUNCHCTL_BIN:-launchctl}"
STAT_BIN="${STAT_BIN:-/usr/bin/stat}"
SLEEP_BIN="${SLEEP_BIN:-sleep}"
INSTALL_LOCK_DIR="${INSTALL_LOCK_DIR:-/tmp/com.resonant.whooshd-launchd-installer.lock}"
LAUNCHD_POLL_ATTEMPTS="${LAUNCHD_POLL_ATTEMPTS:-20}"
LAUNCHD_POLL_INTERVAL_SECONDS="${LAUNCHD_POLL_INTERVAL_SECONDS:-0.25}"
LAUNCHCTL_ABSENT_STATUS="${LAUNCHCTL_ABSENT_STATUS:-113}"

LOCK_HELD=0
QUERY_STATE="indeterminate"
QUERY_STATUS=1
COMMAND_STATUS=1
COMMAND_DIAGNOSTIC=""

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

cleanup_lock() {
  if [[ "$LOCK_HELD" -eq 1 ]]; then
    rmdir "$INSTALL_LOCK_DIR" 2>/dev/null || true
  fi
}

trap cleanup_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

bounded_text() {
  local value="$1"
  value="${value//$'\n'/ }"
  value="${value//$'\r'/ }"
  printf '%s' "${value:0:400}"
}

registration_class() {
  case "$1" in
    registered-running|registered-not-running)
      printf 'registered'
      ;;
    absent)
      printf 'absent'
      ;;
    *)
      printf 'indeterminate'
      ;;
  esac
}

query_service() {
  local label="$1"
  local output

  set +e
  output="$("$LAUNCHCTL_BIN" print "system/$label" 2>/dev/null)"
  QUERY_STATUS=$?
  set -e

  if [[ "$QUERY_STATUS" -eq 0 ]]; then
    # Runtime state is a reporting hint only. Transition decisions depend on
    # the successful exact-target query, not launchctl's human-oriented text.
    if printf '%s\n' "$output" | grep -Eq '^[[:space:]]*state = running[[:space:]]*$'; then
      QUERY_STATE="registered-running"
    else
      QUERY_STATE="registered-not-running"
    fi
  elif [[ "$QUERY_STATUS" -eq "$LAUNCHCTL_ABSENT_STATUS" ]]; then
    QUERY_STATE="absent"
  else
    QUERY_STATE="indeterminate"
  fi
}

interpret_launchctl_error() {
  local status="$1"
  local interpretation

  set +e
  interpretation="$("$LAUNCHCTL_BIN" error "$status" 2>&1)"
  set -e
  bounded_text "$interpretation"
}

run_launchctl_mutation() {
  local stage="$1"
  local label="$2"
  shift 2
  local output

  set +e
  output="$("$SUDO_BIN" "$LAUNCHCTL_BIN" "$@" 2>&1)"
  COMMAND_STATUS=$?
  set -e
  COMMAND_DIAGNOSTIC="$(bounded_text "$output")"

  if [[ "$COMMAND_STATUS" -ne 0 ]]; then
    echo "launchd command returned nonzero: stage=$stage label=$label status=$COMMAND_STATUS"
    echo "launchctl interpretation: $(interpret_launchctl_error "$COMMAND_STATUS")"
    if [[ -n "$COMMAND_DIAGNOSTIC" ]]; then
      echo "bounded diagnostic: $COMMAND_DIAGNOSTIC"
    fi
  fi
}

validate_rendered_network_contract() {
  local whooshd_env_host whooshd_env_port whooshd_arg_host whooshd_arg_port
  local sidecar_arg_host sidecar_arg_port whooshd_args sidecar_args

  whooshd_env_host="$("$PLUTIL_BIN" -extract EnvironmentVariables.WHOOSHD_HOST raw -o - "$WHOOSHD_PLIST")"
  whooshd_env_port="$("$PLUTIL_BIN" -extract EnvironmentVariables.WHOOSHD_PORT raw -o - "$WHOOSHD_PLIST")"
  whooshd_arg_host="$("$PLUTIL_BIN" -extract ProgramArguments.2 raw -o - "$WHOOSHD_PLIST")"
  whooshd_arg_port="$("$PLUTIL_BIN" -extract ProgramArguments.4 raw -o - "$WHOOSHD_PLIST")"
  sidecar_arg_host="$("$PLUTIL_BIN" -extract ProgramArguments.7 raw -o - "$MLX_VLM_PLIST")"
  sidecar_arg_port="$("$PLUTIL_BIN" -extract ProgramArguments.9 raw -o - "$MLX_VLM_PLIST")"
  whooshd_args="$("$PLUTIL_BIN" -extract ProgramArguments json -o - "$WHOOSHD_PLIST")"
  sidecar_args="$("$PLUTIL_BIN" -extract ProgramArguments json -o - "$MLX_VLM_PLIST")"

  if [[ "$whooshd_env_host" != "127.0.0.1" || "$whooshd_arg_host" != "127.0.0.1" || "$whooshd_env_port" != "8000" || "$whooshd_arg_port" != "8000" ]]; then
    echo "Rendered Whoosh'd plist is not the required loopback 127.0.0.1:8000 contract" >&2
    return 1
  fi
  if [[ "$sidecar_arg_host" != "127.0.0.1" || "$sidecar_arg_port" != "8082" ]]; then
    echo "Rendered MLX-VLM plist is not the required loopback 127.0.0.1:8082 contract" >&2
    return 1
  fi
  if [[ "$whooshd_args" == *"--codexify"* || "$whooshd_args" == *"0.0.0.0"* || "$whooshd_args" == *'"::"'* || "$sidecar_args" == *"0.0.0.0"* || "$sidecar_args" == *'"::"'* ]]; then
    echo "Rendered launchd bundle contains a wildcard or --codexify argument" >&2
    return 1
  fi
}

validate_target_path() {
  local target="$1"

  if [[ "$(dirname "$target")" != "$SYSTEM_DIR" ]]; then
    echo "Installed target escapes the configured system directory: $target" >&2
    return 1
  fi
  if [[ -L "$target" ]]; then
    echo "Installed target must not be a symbolic link: $target" >&2
    return 1
  fi
  if [[ -e "$target" && ! -f "$target" ]]; then
    echo "Installed target is not a regular file: $target" >&2
    return 1
  fi
  if [[ -e "$target" && ! -r "$target" ]]; then
    echo "Installed target is not readable for preflight: $target" >&2
    return 1
  fi
}

validate_installed_metadata() {
  local target="$1"
  local metadata

  metadata="$("$STAT_BIN" -f '%Su:%Sg:%Lp' "$target")"
  if [[ "$metadata" != "root:wheel:644" ]]; then
    echo "Installed plist metadata mismatch for $target: expected root:wheel:644, got $metadata" >&2
    return 1
  fi
}

acquire_lock() {
  umask 077
  if ! mkdir "$INSTALL_LOCK_DIR" 2>/dev/null; then
    echo "Another launchd bundle installer is active or left a stale lock: $INSTALL_LOCK_DIR" >&2
    echo "Verify no installer process is running before removing a stale lock." >&2
    return 1
  fi
  LOCK_HELD=1
}

poll_until_absent() {
  local label="$1"
  local attempt

  for ((attempt = 1; attempt <= LAUNCHD_POLL_ATTEMPTS; attempt++)); do
    query_service "$label"
    if [[ "$QUERY_STATE" == "absent" ]]; then
      echo "Removal confirmed: label=$label state=absent attempt=$attempt"
      return 0
    fi
    if [[ "$QUERY_STATE" == "indeterminate" ]]; then
      echo "Removal verification indeterminate: label=$label query_status=$QUERY_STATUS" >&2
      return 1
    fi
    if [[ "$attempt" -lt "$LAUNCHD_POLL_ATTEMPTS" ]]; then
      "$SLEEP_BIN" "$LAUNCHD_POLL_INTERVAL_SECONDS"
    fi
  done

  echo "Removal verification timed out: label=$label state=$QUERY_STATE attempts=$LAUNCHD_POLL_ATTEMPTS" >&2
  return 1
}

remove_if_registered() {
  local label="$1"
  local initial_state="$2"

  if [[ "$(registration_class "$initial_state")" == "absent" ]]; then
    echo "Removal not needed: label=$label state=absent"
    return 0
  fi

  run_launchctl_mutation "bootout" "$label" bootout "system/$label"
  if ! poll_until_absent "$label"; then
    echo "Transition failed: stage=bootout label=$label command_status=$COMMAND_STATUS post_state=$QUERY_STATE" >&2
    return 1
  fi
  if [[ "$COMMAND_STATUS" -ne 0 ]]; then
    echo "Bootout nonzero reconciled by verified absence: label=$label status=$COMMAND_STATUS"
  fi
}

bootstrap_and_reconcile() {
  local label="$1"
  local target="$2"

  run_launchctl_mutation "bootstrap" "$label" bootstrap system "$target"
  query_service "$label"
  local post_class
  post_class="$(registration_class "$QUERY_STATE")"

  if [[ "$post_class" == "registered" ]]; then
    if [[ "$COMMAND_STATUS" -ne 0 ]]; then
      echo "Bootstrap nonzero reconciled by registered state: label=$label status=$COMMAND_STATUS state=$QUERY_STATE"
    else
      echo "Bootstrap confirmed: label=$label state=$QUERY_STATE"
    fi
    return 0
  fi

  echo "Transition failed: stage=bootstrap label=$label command_status=$COMMAND_STATUS post_state=$QUERY_STATE query_status=$QUERY_STATUS" >&2
  return 1
}

report_partial_state() {
  local failed_stage="$1"
  local failed_label="$2"
  local whooshd_state sidecar_state

  query_service "$WHOOSHD_LABEL"
  whooshd_state="$QUERY_STATE"
  query_service "$MLX_VLM_LABEL"
  sidecar_state="$QUERY_STATE"

  echo "Bundle convergence failed: stage=$failed_stage label=$failed_label" >&2
  echo "Bundle state: $WHOOSHD_LABEL=$whooshd_state $MLX_VLM_LABEL=$sidecar_state" >&2
  echo "Safe recovery: resolve the reported launchctl condition, then run the command below; it will reclassify both exact service targets before mutation." >&2
  echo "Recovery command: sudo -v && OUTPUT_DIR=\"$OUTPUT_DIR\" bash \"$SCRIPT_DIR/install_local_launchd.sh\" install" >&2
}

kickstart_and_verify() {
  local label="$1"

  run_launchctl_mutation "kickstart" "$label" kickstart -k "system/$label"
  query_service "$label"
  local post_class
  post_class="$(registration_class "$QUERY_STATE")"

  if [[ "$COMMAND_STATUS" -ne 0 || "$post_class" != "registered" ]]; then
    echo "Transition failed: stage=kickstart label=$label command_status=$COMMAND_STATUS post_state=$QUERY_STATE query_status=$QUERY_STATUS" >&2
    return 1
  fi
  echo "Kickstart retained registration: label=$label state=$QUERY_STATE"
}

if [[ "$MODE" != "dry-run" && "$MODE" != "install" ]]; then
  usage
  exit 2
fi

if [[ ! "$LAUNCHD_POLL_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "LAUNCHD_POLL_ATTEMPTS must be a positive integer" >&2
  exit 2
fi

if [[ ! -f "$WHOOSHD_PLIST" || ! -f "$MLX_VLM_PLIST" ]]; then
  echo "Rendered plists not found in $OUTPUT_DIR"
  echo "Render again with --whooshd-python /absolute/path/to/python; see docs/ops/whooshd-launchd-local-runtime.md"
  exit 1
fi

"$PLUTIL_BIN" -lint "$WHOOSHD_PLIST"
"$PLUTIL_BIN" -lint "$MLX_VLM_PLIST"
"$PYTHON_BIN" "$SCRIPT_DIR/validate_whooshd_python.py" --plist "$WHOOSHD_PLIST"
validate_rendered_network_contract

if [[ ! -d "$SYSTEM_DIR" ]]; then
  echo "System LaunchDaemon directory not found: $SYSTEM_DIR" >&2
  exit 1
fi
validate_target_path "$WHOOSHD_TARGET"
validate_target_path "$MLX_VLM_TARGET"
validate_target_path "$WHOOSHD_BACKUP"
if [[ -e "$WHOOSHD_TARGET" ]]; then
  validate_installed_metadata "$WHOOSHD_TARGET"
fi
if [[ -e "$MLX_VLM_TARGET" ]]; then
  validate_installed_metadata "$MLX_VLM_TARGET"
fi

echo "Validated:"
echo "  $WHOOSHD_PLIST"
echo "  $MLX_VLM_PLIST"
echo "  target directory: $SYSTEM_DIR"

if [[ "$MODE" == "dry-run" ]]; then
  echo
  echo "Dry run only. No lock, privileged command, or launchd query was executed."
  echo "Install mode will acquire: $INSTALL_LOCK_DIR"
  echo "Install mode will classify, remove, bootstrap, reconcile, and kickstart:"
  echo "  system/$WHOOSHD_LABEL"
  echo "  system/$MLX_VLM_LABEL"
  exit 0
fi

acquire_lock

query_service "$WHOOSHD_LABEL"
WHOOSHD_INITIAL_STATE="$QUERY_STATE"
query_service "$MLX_VLM_LABEL"
MLX_VLM_INITIAL_STATE="$QUERY_STATE"

echo "Initial bundle state: $WHOOSHD_LABEL=$WHOOSHD_INITIAL_STATE $MLX_VLM_LABEL=$MLX_VLM_INITIAL_STATE"
if [[ "$(registration_class "$WHOOSHD_INITIAL_STATE")" == "indeterminate" || "$(registration_class "$MLX_VLM_INITIAL_STATE")" == "indeterminate" ]]; then
  echo "Refusing mutation because initial launchd state is indeterminate" >&2
  exit 1
fi

if [[ -f "$WHOOSHD_TARGET" ]]; then
  "$SUDO_BIN" cp "$WHOOSHD_TARGET" "$WHOOSHD_BACKUP"
fi
"$SUDO_BIN" cp "$WHOOSHD_PLIST" "$WHOOSHD_TARGET"
"$SUDO_BIN" cp "$MLX_VLM_PLIST" "$MLX_VLM_TARGET"
"$SUDO_BIN" chown root:wheel "$WHOOSHD_TARGET" "$MLX_VLM_TARGET"
"$SUDO_BIN" chmod 644 "$WHOOSHD_TARGET" "$MLX_VLM_TARGET"
validate_installed_metadata "$WHOOSHD_TARGET"
validate_installed_metadata "$MLX_VLM_TARGET"

if ! remove_if_registered "$WHOOSHD_LABEL" "$WHOOSHD_INITIAL_STATE"; then
  report_partial_state "bootout" "$WHOOSHD_LABEL"
  exit 1
fi
if ! remove_if_registered "$MLX_VLM_LABEL" "$MLX_VLM_INITIAL_STATE"; then
  report_partial_state "bootout" "$MLX_VLM_LABEL"
  exit 1
fi

if ! bootstrap_and_reconcile "$WHOOSHD_LABEL" "$WHOOSHD_TARGET"; then
  report_partial_state "bootstrap" "$WHOOSHD_LABEL"
  exit 1
fi
if ! bootstrap_and_reconcile "$MLX_VLM_LABEL" "$MLX_VLM_TARGET"; then
  report_partial_state "bootstrap" "$MLX_VLM_LABEL"
  exit 1
fi

query_service "$WHOOSHD_LABEL"
WHOOSHD_REGISTERED_STATE="$QUERY_STATE"
query_service "$MLX_VLM_LABEL"
MLX_VLM_REGISTERED_STATE="$QUERY_STATE"
if [[ "$(registration_class "$WHOOSHD_REGISTERED_STATE")" != "registered" || "$(registration_class "$MLX_VLM_REGISTERED_STATE")" != "registered" ]]; then
  report_partial_state "pre-kickstart-gate" "bundle"
  exit 1
fi

if ! kickstart_and_verify "$WHOOSHD_LABEL"; then
  report_partial_state "kickstart" "$WHOOSHD_LABEL"
  exit 1
fi
if ! kickstart_and_verify "$MLX_VLM_LABEL"; then
  report_partial_state "kickstart" "$MLX_VLM_LABEL"
  exit 1
fi

query_service "$WHOOSHD_LABEL"
WHOOSHD_FINAL_STATE="$QUERY_STATE"
query_service "$MLX_VLM_LABEL"
MLX_VLM_FINAL_STATE="$QUERY_STATE"
if [[ "$(registration_class "$WHOOSHD_FINAL_STATE")" != "registered" || "$(registration_class "$MLX_VLM_FINAL_STATE")" != "registered" ]]; then
  report_partial_state "final-registration-gate" "bundle"
  exit 1
fi

echo "Install converged:"
echo "  $WHOOSHD_LABEL=$WHOOSHD_FINAL_STATE"
echo "  $MLX_VLM_LABEL=$MLX_VLM_FINAL_STATE"
echo "Runtime health, inventory, generation, and containment require the separate live proof task."

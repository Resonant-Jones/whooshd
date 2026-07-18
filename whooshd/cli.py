"""Command-line interface for the Whoosh'd local broker."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from whooshd.model_registry.imports import (
    format_local_mlx_import_report,
    import_local_mlx_models,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MLX_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
DEFAULT_WAIT_TIMEOUT = 30.0
FORCE_WAIT_TIMEOUT = 2.0


def state_dir() -> Path:
    return Path.home() / ".whooshd"


def pid_path() -> Path:
    return state_dir() / "whooshd.pid"


def log_path() -> Path:
    return state_dir() / "whooshd.log"


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_process_group(pgid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def wait_for_process_group_exit(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_group_alive(pgid):
            return True
        time.sleep(0.2)
    return not is_process_group_alive(pgid)


def read_tracked_pid() -> int | None:
    try:
        raw = pid_path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_tracked_pid(pid: int) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    pid_path().write_text(f"{pid}\n", encoding="utf-8")


def clear_tracked_pid() -> None:
    try:
        pid_path().unlink()
    except FileNotFoundError:
        pass


def is_port_occupied(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def build_uvicorn_command(host: str, port: int, reload: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "whooshd.app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        command.append("--reload")
    return command


def build_server_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("WHOOSHD_MLX_ENABLED", "true")
    env.setdefault("WHOOSHD_MLX_MODEL", DEFAULT_MLX_MODEL)
    if args.model:
        env["WHOOSHD_MLX_MODEL"] = args.model
    if args.adapter:
        env["WHOOSHD_ADAPTER"] = args.adapter
    if args.mlx is not None:
        env["WHOOSHD_MLX_ENABLED"] = "true" if args.mlx else "false"
    return env


def launch_server(command: list[str], env: dict[str, str]) -> subprocess.Popen:
    state_dir().mkdir(parents=True, exist_ok=True)
    log_file = log_path().open("ab")
    return subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def fetch_json(host: str, port: int, path: str, timeout: float = 2.0) -> tuple[int | None, str]:
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def _parse_json_body(raw: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _display_env_value(raw: str | None) -> str:
    return raw if raw else "<unset>"


def _extract_inventory_model_ids(payload: dict[str, object] | None) -> list[str]:
    if not payload:
        return []

    entries = payload.get("data")
    if not isinstance(entries, list):
        entries = payload.get("models")
    if not isinstance(entries, list):
        return []

    model_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id") or entry.get("name")
        if isinstance(model_id, str) and model_id:
            model_ids.append(model_id)
    return model_ids


def _looks_like_local_path(model_id: str) -> bool:
    return model_id.startswith(("/", "./", "../", "~/"))


def _summarize_model_store(store_root_raw: str | None) -> tuple[str, str]:
    if not store_root_raw:
        return "unset", "WHOOSHD_MODEL_STORE_ROOT is not set."

    store_root = Path(store_root_raw).expanduser()
    if not store_root.exists():
        return (
            "missing_root",
            f"WHOOSHD_MODEL_STORE_ROOT points to {store_root}, but that path does not exist.",
        )

    manifest_path = store_root / "registry" / "models.json"
    if not manifest_path.exists():
        return (
            "missing_manifest",
            f"{manifest_path} is missing, so the model-store has not been bootstrapped.",
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            "invalid_manifest",
            f"{manifest_path} is unreadable: {exc}",
        )

    if not isinstance(manifest, dict):
        return (
            "invalid_manifest",
            f"{manifest_path} is not a JSON object.",
        )

    models = manifest.get("models")
    if not isinstance(models, list):
        return (
            "invalid_manifest",
            f"{manifest_path} does not contain a models array.",
        )
    if not models:
        return (
            "empty_manifest",
            f"{manifest_path} exists, but it does not register any models yet.",
        )

    return (
        "has_models",
        f"{manifest_path} exists and contains {len(models)} registered model(s).",
    )


def wait_until_reachable(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _ = fetch_json(host, port, "/health", timeout=1.0)
        if status == 200:
            return True
        time.sleep(0.25)
    return False


def start_server(args: argparse.Namespace) -> int:
    tracked_pid = read_tracked_pid()
    if tracked_pid is not None:
        if is_process_alive(tracked_pid):
            print(f"Whoosh'd is already running with PID {tracked_pid}.")
            return 0
        print(f"Removing stale Whoosh'd PID file for PID {tracked_pid}.")
        clear_tracked_pid()

    if is_port_occupied(args.host, args.port):
        print(
            f"Port {args.port} on {args.host} is already in use, but no tracked "
            "Whoosh'd PID exists.",
            file=sys.stderr,
        )
        print(
            f"Inspect the listener with: lsof -nP -iTCP:{args.port} -sTCP:LISTEN",
            file=sys.stderr,
        )
        return 2

    command = build_uvicorn_command(args.host, args.port, args.reload)
    process = launch_server(command, build_server_env(args))
    write_tracked_pid(process.pid)
    print(f"Started Whoosh'd with PID {process.pid}.")
    print(f"Logs: {log_path()}")

    if args.no_wait:
        return 0
    if wait_until_reachable(args.host, args.port, args.timeout):
        print("Whoosh'd health endpoint is reachable.")
        return 0
    print(
        f"Whoosh'd did not report healthy within {args.timeout:g}s. "
        "Check logs for startup details.",
        file=sys.stderr,
    )
    return 1


def stop_server(args: argparse.Namespace) -> int:
    tracked_pid = read_tracked_pid()
    if tracked_pid is None:
        print("No tracked Whoosh'd PID found.")
        return 0

    process_alive = is_process_alive(tracked_pid)
    group_alive = is_process_group_alive(tracked_pid)
    if not process_alive and not group_alive:
        print(f"Tracked Whoosh'd PID {tracked_pid} is stale; removing PID file.")
        clear_tracked_pid()
        return 0

    signal_process_group(tracked_pid, signal.SIGTERM)
    if wait_for_process_group_exit(tracked_pid, args.timeout):
        clear_tracked_pid()
        print(f"Stopped Whoosh'd process group {tracked_pid}.")
        return 0

    if args.force:
        signal_process_group(tracked_pid, signal.SIGKILL)
        if wait_for_process_group_exit(tracked_pid, FORCE_WAIT_TIMEOUT):
            clear_tracked_pid()
            print(f"Force-stopped Whoosh'd process group {tracked_pid}.")
            return 0
        print(
            f"Whoosh'd process group {tracked_pid} did not stop after SIGKILL; "
            f"leaving {pid_path()} for diagnosis.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Whoosh'd process group {tracked_pid} did not stop within {args.timeout:g}s. "
        f"Leaving {pid_path()} in place; re-run with --force if this is the "
        "tracked process family you want to kill.",
        file=sys.stderr,
    )
    return 1


def status_server(args: argparse.Namespace) -> int:
    tracked_pid = read_tracked_pid()
    alive = tracked_pid is not None and is_process_alive(tracked_pid)
    group_alive = tracked_pid is not None and is_process_group_alive(tracked_pid)
    print(f"Tracked PID: {tracked_pid if tracked_pid is not None else 'none'}")
    print(f"Process alive: {'yes' if alive else 'no'}")
    print(f"Process group alive: {'yes' if group_alive else 'no'}")
    if tracked_pid is not None and not alive and not group_alive:
        print("Tracking state: stale")

    exit_code = 0
    for path in ("/health", "/ready", "/v1/models"):
        status, body = fetch_json(args.host, args.port, path)
        label = path.removeprefix("/")
        if status is None:
            print(f"{label}: unreachable ({body})")
            exit_code = 1
        else:
            print(f"{label}: HTTP {status}")
            if args.verbose:
                print(body)
            if path in ("/health", "/ready") and status >= 500:
                exit_code = 1

    if tracked_pid is not None and not group_alive:
        exit_code = 1
    return exit_code


def doctor_server(args: argparse.Namespace) -> int:
    tracked_pid = read_tracked_pid()
    process_alive = tracked_pid is not None and is_process_alive(tracked_pid)
    group_alive = tracked_pid is not None and is_process_group_alive(tracked_pid)

    probes: dict[str, dict[str, object]] = {}
    for path in ("/health", "/ready", "/v1/models"):
        status, body = fetch_json(args.host, args.port, path)
        probes[path] = {
            "status": status,
            "body": body,
            "payload": _parse_json_body(body),
        }

    health_payload = probes["/health"]["payload"] if isinstance(probes["/health"]["payload"], dict) else {}
    ready_payload = probes["/ready"]["payload"] if isinstance(probes["/ready"]["payload"], dict) else {}
    models_payload = probes["/v1/models"]["payload"] if isinstance(probes["/v1/models"]["payload"], dict) else {}
    inventory_ids = _extract_inventory_model_ids(models_payload)

    health_status = probes["/health"]["status"]
    ready_status = probes["/ready"]["status"]
    models_status = probes["/v1/models"]["status"]

    adapter_backend = os.environ.get("WHOOSHD_ADAPTER", "stub")
    mlx_enabled = os.environ.get("WHOOSHD_MLX_ENABLED")
    mlx_model = os.environ.get("WHOOSHD_MLX_MODEL")
    store_root_raw = os.environ.get("WHOOSHD_MODEL_STORE_ROOT")
    store_code, store_note = _summarize_model_store(store_root_raw)

    ready_configured_model = ready_payload.get("configured_model") if isinstance(ready_payload.get("configured_model"), str) else None
    health_active_model = health_payload.get("active_model") if isinstance(health_payload.get("active_model"), str) else None
    live_configured_model = ready_configured_model or health_active_model
    expected_model = mlx_model or live_configured_model

    print("Whoosh'd doctor")
    print()
    print("Tracked daemon:")
    print(f"  PID: {tracked_pid if tracked_pid is not None else 'none'}")
    print(f"  Process alive: {'yes' if process_alive else 'no'}")
    print(f"  Process group alive: {'yes' if group_alive else 'no'}")
    if tracked_pid is not None and not process_alive and not group_alive:
        print("  Tracking state: stale")

    print()
    print("Environment:")
    print(f"  WHOOSHD_ADAPTER={_display_env_value(os.environ.get('WHOOSHD_ADAPTER'))}")
    print(f"  WHOOSHD_MLX_ENABLED={_display_env_value(mlx_enabled)}")
    print(f"  WHOOSHD_MLX_MODEL={_display_env_value(mlx_model)}")
    print(f"  WHOOSHD_MODEL_STORE_ROOT={_display_env_value(store_root_raw)}")

    print()
    print("Model-store:")
    if store_root_raw:
        print(f"  {Path(store_root_raw).expanduser()}")
    else:
        print("  <unset>")
    print(f"  {store_note}")

    print()
    print("Probes:")
    if health_status is None:
        print(f"  /health: unreachable ({probes['/health']['body']})")
    else:
        print(f"  /health: HTTP {health_status}")
        status_value = health_payload.get("status")
        lifecycle = health_payload.get("model_lifecycle")
        active_model = health_payload.get("active_model")
        print(
            "    "
            f"status={status_value if status_value is not None else 'None'} "
            f"model_lifecycle={lifecycle if lifecycle is not None else 'None'} "
            f"active_model={active_model if active_model is not None else 'None'}"
        )

    if ready_status is None:
        print(f"  /ready: unreachable ({probes['/ready']['body']})")
    else:
        print(f"  /ready: HTTP {ready_status}")
        ready_flag = ready_payload.get("ready")
        configured_model = ready_payload.get("configured_model")
        loaded_model = ready_payload.get("loaded_model")
        reason = ready_payload.get("reason")
        ready_reason = f" reason={reason}" if reason else ""
        print(
            "    "
            f"ready={'yes' if ready_flag else 'no'} "
            f"configured_model={configured_model if configured_model is not None else 'None'} "
            f"loaded_model={loaded_model if loaded_model is not None else 'None'}"
            f"{ready_reason}"
        )

    if models_status is None:
        print(f"  /v1/models: unreachable ({probes['/v1/models']['body']})")
    elif inventory_ids:
        preview = ", ".join(inventory_ids[:3])
        if len(inventory_ids) > 3:
            preview = f"{preview}, ..."
        expected_note = ""
        if expected_model:
            expected_note = (
                "; expected model advertised"
                if expected_model in inventory_ids
                else "; expected model missing"
            )
        print(
            f"  /v1/models: HTTP {models_status} "
            f"({len(inventory_ids)} model{'s' if len(inventory_ids) != 1 else ''}: {preview}{expected_note})"
        )
    else:
        print(f"  /v1/models: HTTP {models_status} (no advertised models)")

    diagnosis = "healthy"
    cause = None
    next_steps: list[str] = []

    if tracked_pid is not None and not process_alive and not group_alive:
        diagnosis = "stale_pid"
        next_steps = [
            "Run `whoosh down` to clear the stale PID file.",
            "Start the daemon again with `whoosh up`.",
        ]
    elif health_status is None:
        diagnosis = "daemon_unreachable"
        next_steps = [
            "Confirm the daemon is still running and listening on the requested host and port.",
            "Check `whoosh logs` for startup failures, then re-run `whoosh up` if needed.",
        ]
    else:
        inventory_missing = bool(expected_model and expected_model not in inventory_ids)
        inventory_empty = len(inventory_ids) == 0

        if inventory_empty or inventory_missing:
            diagnosis = "local_model_resolution_error"
            if expected_model and _looks_like_local_path(expected_model) and not Path(expected_model).expanduser().exists():
                cause = "bad_local_path"
                next_steps = [
                    f"Fix `WHOOSHD_MLX_MODEL={expected_model!r}` so it points at an existing local path, then restart the daemon.",
                ]
            elif mlx_model and adapter_backend != "mlx":
                cause = "configured_model_not_advertised_by_whooshd"
                next_steps = [
                    f"Start the daemon with `whoosh up --adapter mlx` or export `WHOOSHD_ADAPTER=mlx` before launch so the configured model is advertised instead of the current `WHOOSHD_ADAPTER={adapter_backend}` value.",
                    "If you are relying on the model-store, make sure the configured model is registered and advertised.",
                ]
            elif inventory_empty and store_code in {"missing_root", "missing_manifest"}:
                cause = "model_store_not_bootstrapped"
                next_steps = [
                    f"Bootstrap the model-store at {store_root_raw!r} with `bootstrap_model_store()`.",
                    "Register or move a model into the managed store so it appears in `/v1/models`.",
                ]
            elif inventory_empty and store_code == "empty_manifest":
                cause = "no_advertised_models"
                next_steps = [
                    "Register at least one model in the model-store so `/v1/models` returns a model entry.",
                ]
            elif inventory_empty:
                cause = "no_advertised_models"
                next_steps = [
                    "Make sure the active adapter advertises at least one model.",
                ]
                if store_code == "has_models":
                    next_steps.append(
                        "If the store is supposed to contribute models, verify those registrations are advertisable.",
                    )
            else:
                cause = "configured_model_not_advertised_by_whooshd"
                next_steps = [
                    f"Change `WHOOSHD_MLX_MODEL` to an advertised model or register {expected_model!r} so it appears in `/v1/models`.",
                ]
                if store_code == "missing_manifest":
                    next_steps.append(
                        f"Bootstrap the model-store at {store_root_raw!r} before re-checking inventory.",
                    )
        elif ready_status is not None and ready_payload.get("ready") is False:
            diagnosis = "provider_not_ready"
            reason = ready_payload.get("reason") or "not_ready"
            cause = str(reason)
            next_steps = [
                f"Resolve the readiness reason reported by `/ready`: {reason}.",
                "Re-run `whoosh doctor` after the provider becomes ready.",
            ]

    print()
    print("Diagnosis:")
    print(f"  {diagnosis}")
    if cause:
        print(f"  cause: {cause}")

    if next_steps:
        print()
        print("Next step:")
        for index, step in enumerate(next_steps, start=1):
            print(f"  {index}. {step}")
    elif diagnosis == "healthy":
        print()
        print("Next step:")
        print("  1. No likely resolution issue found.")

    return 0 if diagnosis == "healthy" else 1


def show_logs(args: argparse.Namespace) -> int:
    path = log_path()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        print(f"No Whoosh'd log file found at {path}.")
        return 0
    for line in lines[-args.tail :]:
        print(line)
    return 0


def import_models(args: argparse.Namespace) -> int:
    report = import_local_mlx_models(
        store_root=args.store_root,
        scan_roots=args.scan_root,
    )
    print(format_local_mlx_import_report(report))
    return 1 if report.error else 0


def _add_up_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model")
    parser.add_argument("--adapter")
    mlx = parser.add_mutually_exclusive_group()
    mlx.add_argument("--mlx", dest="mlx", action="store_true", default=None)
    mlx.add_argument("--no-mlx", dest="mlx", action="store_false")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_WAIT_TIMEOUT)
    parser.add_argument("--no-wait", action="store_true")
    parser.set_defaults(func=start_server)


def _add_down_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--force", action="store_true")
    parser.set_defaults(func=stop_server)


def _add_import_models_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store-root",
        help=(
            "Managed model-store root. Defaults to WHOOSHD_MODEL_STORE_ROOT "
            "or ~/whooshd-models."
        ),
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        dest="scan_root",
        help=(
            "Local cache root to scan for MLX snapshots. Repeat to add more "
            "roots; defaults to the common Hugging Face cache locations."
        ),
    )
    parser.set_defaults(func=import_models)


def _build_help_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Whoosh'd local inference broker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Common commands:
  whoosh -d        Start the daemon
  whoosh down      Stop the daemon
  whoosh status    Check health/readiness
  whoosh doctor    Diagnose model resolution issues
  whoosh logs      Show logs
  whoosh import-models  Import local MLX cache snapshots into the managed store

Alternate entrypoints:
  whooshd-up       Same as whoosh -d
  whooshd-down     Same as whoosh down
  whoosh up        Same as whoosh -d
  whooshd up       Same as whoosh -d
  whooshd doctor   Same as whoosh doctor
""",
    )
    parser.add_argument("-d", "--daemon", action="store_true", help="Start the daemon")
    subparsers = parser.add_subparsers(dest="command")

    up = subparsers.add_parser("up", help="Start the daemon")
    _add_up_options(up)

    down = subparsers.add_parser("down", help="Stop the daemon")
    _add_down_options(down)

    status = subparsers.add_parser("status", help="Check health/readiness")
    status.add_argument("--host", default=DEFAULT_HOST)
    status.add_argument("--port", type=int, default=DEFAULT_PORT)
    status.add_argument("--verbose", action="store_true")
    status.set_defaults(func=status_server)

    doctor = subparsers.add_parser("doctor", help="Diagnose model resolution issues")
    doctor.add_argument("--host", default=DEFAULT_HOST)
    doctor.add_argument("--port", type=int, default=DEFAULT_PORT)
    doctor.set_defaults(func=doctor_server)

    logs = subparsers.add_parser("logs", help="Show logs")
    logs.add_argument("--tail", type=int, default=80)
    logs.set_defaults(func=show_logs)

    import_models_parser = subparsers.add_parser(
        "import-models",
        help="Import local MLX cache snapshots into the managed store",
    )
    _add_import_models_options(import_models_parser)

    return parser


def build_parser(prog: str = "whoosh") -> argparse.ArgumentParser:
    return _build_help_parser(prog)


def _parse_up_args(argv: Sequence[str], prog: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
    _add_up_options(parser)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    prog = Path(sys.argv[0]).name or "whoosh"

    if args_list and args_list[0] in ("-d", "--daemon"):
        args = _parse_up_args(args_list[1:], f"{prog} -d")
        return args.func(args)

    parser = build_parser(prog)
    args = parser.parse_args(args_list)
    if args.daemon:
        up_args = _parse_up_args([], f"{prog} -d")
        return up_args.func(up_args)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


def up_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_up_args(sys.argv[1:] if argv is None else argv, "whooshd-up")
    return args.func(args)


def down_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whooshd-down")
    _add_down_options(parser)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

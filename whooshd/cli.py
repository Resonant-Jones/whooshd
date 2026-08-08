"""Command-line interface for the Whoosh'd local broker."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path

from whooshd.log_safety import exception_metadata


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MLX_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
DEFAULT_WAIT_TIMEOUT = 30.0
FORCE_WAIT_TIMEOUT = 2.0


def state_dir() -> Path:
    return Path.home() / ".whooshd"


def pid_path() -> Path:
    return state_dir() / "whooshd.pid"


def launch_nonce_path() -> Path:
    return state_dir() / "whooshd.launch-nonce"


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


def read_launch_nonce() -> str | None:
    try:
        value = launch_nonce_path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def write_launch_nonce(nonce: str) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    launch_nonce_path().write_text(f"{nonce}\n", encoding="utf-8")


def write_tracked_pid(pid: int) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    pid_path().write_text(f"{pid}\n", encoding="utf-8")


def clear_tracked_pid() -> None:
    try:
        pid_path().unlink()
    except FileNotFoundError:
        pass
    try:
        launch_nonce_path().unlink()
    except FileNotFoundError:
        pass


def _process_group_member_pids(pgid: int) -> list[str]:
    """Return process IDs for members of a tracked process group.

    ``start_new_session=True`` makes the launched process PID its process-group
    ID.  The leader can exit while a reloader child remains, so inspecting only
    the original PID is not sufficient for safe lifecycle control.
    """
    try:
        members = subprocess.run(
            ["pgrep", "-g", str(pgid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if members.returncode != 0:
        return []

    return [raw_pid for raw_pid in members.stdout.split() if raw_pid.isdigit()]


def _process_command(pid: str, *, include_environment: bool = False) -> str | None:
    """Return a process command, optionally including its environment."""
    command = ["ps"]
    if include_environment:
        command.append("eww")
    command.extend(["-p", pid, "-o", "command="])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _is_whooshd_server_command(command: str) -> bool:
    """Return whether a process command matches the CLI's Uvicorn launch."""
    return "uvicorn" in command and "whooshd.app:app" in command


def process_matches_launch_nonce(pgid: int, nonce: str | None) -> bool:
    """Verify a tracked process group contains our nonce-bearing daemon."""
    if not nonce:
        return False
    for pid in _process_group_member_pids(pgid):
        command = _process_command(pid)
        if not command or not _is_whooshd_server_command(command):
            continue
        environment_command = _process_command(pid, include_environment=True)
        if (
            environment_command
            and f"WHOOSHD_LAUNCH_NONCE={nonce}" in environment_command
        ):
            return True
    return False


def process_matches_legacy_daemon(pgid: int) -> bool:
    """Verify a pre-nonce Whoosh'd process group for one-time compatibility.

    Legacy control is available only when no nonce file exists.  It still
    requires an expected Uvicorn Whoosh'd command in the tracked group; an
    arbitrary live PID or process group remains outside the CLI's authority.
    """
    return any(
        _is_whooshd_server_command(command)
        for pid in _process_group_member_pids(pgid)
        if (command := _process_command(pid))
    )


def process_matches_tracked_daemon(pgid: int) -> bool:
    """Verify a nonce-bearing daemon or a narrowly defined legacy daemon."""
    nonce = read_launch_nonce()
    return process_matches_launch_nonce(pgid, nonce) or (
        nonce is None and process_matches_legacy_daemon(pgid)
    )


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
    """Fetch endpoint metadata without retaining or returning its body."""
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            return response.status, f"body_bytes={len(body)}"
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, f"body_bytes={len(body)}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, exception_metadata(exc)


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
        process_alive = is_process_alive(tracked_pid)
        group_alive = is_process_group_alive(tracked_pid)
        if process_alive or group_alive:
            if process_matches_tracked_daemon(tracked_pid):
                print(
                    f"Whoosh'd is already running with tracked process group "
                    f"{tracked_pid}."
                )
                return 0
            print(
                f"Tracked process group {tracked_pid} is alive but is not the "
                "recorded Whoosh'd process family; refusing to control it.",
                file=sys.stderr,
            )
            return 2
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
    launch_nonce = uuid.uuid4().hex
    env = build_server_env(args)
    env["WHOOSHD_LAUNCH_NONCE"] = launch_nonce
    process = launch_server(command, env)
    write_tracked_pid(process.pid)
    write_launch_nonce(launch_nonce)
    print(f"Started Whoosh'd with PID {process.pid}.")
    print("Logs: available=True")

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

    if not process_matches_tracked_daemon(tracked_pid):
        print(
            f"Tracked process group {tracked_pid} is not verified as the "
            "recorded Whoosh'd process family; refusing to signal it.",
            file=sys.stderr,
        )
        return 2

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
            print(f"{label}: unreachable (transport_failure)")
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


def show_logs(args: argparse.Namespace) -> int:
    path = log_path()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        print("No Whoosh'd log file found (logs_available=False).")
        return 0
    for line in lines[-args.tail :]:
        print(line)
    return 0


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


def _build_help_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Whoosh'd local inference broker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Common commands:
  whoosh -d        Start the daemon
  whoosh down      Stop the daemon
  whoosh status    Check health/readiness
  whoosh logs      Show logs

Alternate entrypoints:
  whooshd-up       Same as whoosh -d
  whooshd-down     Same as whoosh down
  whoosh up        Same as whoosh -d
  whooshd up       Same as whoosh -d
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

    logs = subparsers.add_parser("logs", help="Show logs")
    logs.add_argument("--tail", type=int, default=80)
    logs.set_defaults(func=show_logs)

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

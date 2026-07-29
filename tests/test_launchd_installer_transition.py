from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "ops" / "launchd" / "install_local_launchd.sh"
WHOOSHD_LABEL = "com.resonant.whooshd"
SIDECAR_LABEL = "com.resonant.mlx-vlm-gemma12b"


def _make_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_bundle(output_dir: Path, interpreter: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / f"{WHOOSHD_LABEL}.plist").open("wb") as handle:
        plistlib.dump(
            {
                "Label": WHOOSHD_LABEL,
                "ProgramArguments": [
                    "/usr/local/bin/whooshd",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                "EnvironmentVariables": {
                    "WHOOSHD_PYTHON": str(interpreter),
                    "WHOOSHD_ROOT": str(REPO_ROOT),
                    "WHOOSHD_HOST": "127.0.0.1",
                    "WHOOSHD_PORT": "8000",
                },
            },
            handle,
        )
    with (output_dir / f"{SIDECAR_LABEL}.plist").open("wb") as handle:
        plistlib.dump(
            {
                "Label": SIDECAR_LABEL,
                "ProgramArguments": [
                    str(interpreter),
                    "-m",
                    "mlx_vlm",
                    "server",
                    "--model",
                    "/fixture/model",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8082",
                ],
            },
            handle,
        )


@pytest.fixture
def installer_env(tmp_path: Path) -> dict[str, str]:
    if shutil.which("plutil") is None:
        pytest.skip("launchd installer requires plutil")

    fake_bin = tmp_path / "bin"
    event_log = tmp_path / "events.log"
    state_file = tmp_path / "state.json"
    output_dir = tmp_path / "rendered"
    system_dir = tmp_path / "LaunchDaemons"
    system_dir.mkdir()

    fake_interpreter = _make_executable(
        fake_bin / "selected-python",
        "#!/bin/sh\nprintf 'WHOOSHD_PYTHON_PREFLIGHT_OK:/fixture/python\\n'\n",
    )
    _write_bundle(output_dir, fake_interpreter)

    fake_launchctl = _make_executable(
        fake_bin / "launchctl",
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE_FILE"])
log_path = Path(os.environ["FAKE_EVENT_LOG"])

with log_path.open("a") as handle:
    handle.write("launchctl " + " ".join(args) + "\\n")

state = json.loads(state_path.read_text())

def save():
    state_path.write_text(json.dumps(state, sort_keys=True))

def label_from_target(value):
    return value.rsplit("/", 1)[-1]

command = args[0]
if command == "print":
    label = label_from_target(args[1])
    value = state.get(label, "absent")
    if value == "absent":
        raise SystemExit(113)
    if value == "indeterminate":
        raise SystemExit(70)
    runtime = "running" if value == "registered_running" else "waiting"
    print("system/" + label + " = {{")
    print("    state = " + runtime)
    print("}}")
    raise SystemExit(0)

if command == "error":
    print("Input/output error")
    raise SystemExit(0)

if command == "bootout":
    label = label_from_target(args[-1])
    if os.environ.get("BOOTOUT_INDETERMINATE_LABEL") == label:
        state[label] = "indeterminate"
    elif os.environ.get("BOOTOUT_STICKY_LABEL") != label:
        state[label] = "absent"
    save()
    raise SystemExit(0)

if command == "bootstrap":
    label = Path(args[-1]).stem
    if os.environ.get("BOOTSTRAP_NONZERO_REGISTERED_LABEL") == label:
        state[label] = "registered_not_running"
        save()
        print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
        raise SystemExit(5)
    if os.environ.get("BOOTSTRAP_NONZERO_ABSENT_LABEL") == label:
        state[label] = "absent"
        save()
        print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
        raise SystemExit(5)
    if os.environ.get("BOOTSTRAP_ZERO_ABSENT_LABEL") == label:
        state[label] = "absent"
        save()
        raise SystemExit(0)
    state[label] = "registered_not_running"
    save()
    raise SystemExit(0)

if command == "kickstart":
    label = label_from_target(args[-1])
    if state.get(label, "absent").startswith("registered"):
        state[label] = "registered_running"
        save()
        raise SystemExit(0)
    raise SystemExit(113)

raise SystemExit(64)
""",
    )
    fake_sudo = _make_executable(
        fake_bin / "sudo",
        """#!/bin/sh
printf 'sudo %s\n' "$*" >> "$FAKE_EVENT_LOG"
case "$1" in
  cp)
    exec /bin/cp "$2" "$3"
    ;;
  chmod)
    exec /bin/chmod "$2" "$3" "$4"
    ;;
  chown)
    exit 0
    ;;
  *)
    exec "$@"
    ;;
esac
""",
    )
    fake_stat = _make_executable(
        fake_bin / "stat",
        "#!/bin/sh\nprintf '%s\\n' \"${FAKE_STAT_OUTPUT:-root:wheel:644}\"\n",
    )

    state_file.write_text(
        json.dumps({WHOOSHD_LABEL: "absent", SIDECAR_LABEL: "absent"})
    )

    return {
        **os.environ,
        "OUTPUT_DIR": str(output_dir),
        "SYSTEM_DIR": str(system_dir),
        "INSTALL_LOCK_DIR": str(tmp_path / "installer.lock"),
        "SUDO_BIN": str(fake_sudo),
        "LAUNCHCTL_BIN": str(fake_launchctl),
        "STAT_BIN": str(fake_stat),
        "PYTHON_BIN": sys.executable,
        "FAKE_STATE_FILE": str(state_file),
        "FAKE_EVENT_LOG": str(event_log),
        "LAUNCHD_POLL_ATTEMPTS": "2",
        "LAUNCHD_POLL_INTERVAL_SECONDS": "0",
    }


def _set_state(env: dict[str, str], whooshd: str, sidecar: str) -> None:
    Path(env["FAKE_STATE_FILE"]).write_text(
        json.dumps({WHOOSHD_LABEL: whooshd, SIDECAR_LABEL: sidecar})
    )


def _state(env: dict[str, str]) -> dict[str, str]:
    return json.loads(Path(env["FAKE_STATE_FILE"]).read_text())


def _events(env: dict[str, str]) -> list[str]:
    path = Path(env["FAKE_EVENT_LOG"])
    return path.read_text().splitlines() if path.exists() else []


def _run(env: dict[str, str], mode: str = "install") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALLER), mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_both_services_initially_absent_converge(installer_env: dict[str, str]) -> None:
    result = _run(installer_env)

    assert result.returncode == 0, result.stderr
    assert _state(installer_env) == {
        WHOOSHD_LABEL: "registered_running",
        SIDECAR_LABEL: "registered_running",
    }
    assert "Removal not needed" in result.stdout
    assert "Install converged" in result.stdout
    assert not any(" bootout " in event for event in _events(installer_env))


def test_both_registered_are_removed_then_reinstalled(installer_env: dict[str, str]) -> None:
    _set_state(installer_env, "registered_running", "registered_not_running")

    result = _run(installer_env)
    events = _events(installer_env)

    assert result.returncode == 0, result.stderr
    assert "registered-running" in result.stdout
    assert "registered-not-running" in result.stdout
    assert sum(" bootout " in event for event in events) == 4
    assert max(i for i, event in enumerate(events) if " bootout " in event) < min(
        i for i, event in enumerate(events) if " bootstrap " in event
    )


def test_one_registered_and_one_absent_is_reconciled(installer_env: dict[str, str]) -> None:
    _set_state(installer_env, "registered_running", "absent")

    result = _run(installer_env)
    bootouts = [event for event in _events(installer_env) if " bootout " in event]

    assert result.returncode == 0, result.stderr
    assert len(bootouts) == 2  # sudo wrapper and fake launchctl log the same command
    assert WHOOSHD_LABEL in bootouts[0]
    assert SIDECAR_LABEL not in "\n".join(bootouts)


def test_repeated_installation_converges(installer_env: dict[str, str]) -> None:
    first = _run(installer_env)
    second = _run(installer_env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert _state(installer_env) == {
        WHOOSHD_LABEL: "registered_running",
        SIDECAR_LABEL: "registered_running",
    }


def test_bootout_success_requires_confirmed_absence(installer_env: dict[str, str]) -> None:
    _set_state(installer_env, "registered_running", "absent")

    result = _run(installer_env)

    assert result.returncode == 0, result.stderr
    assert f"Removal confirmed: label={WHOOSHD_LABEL} state=absent" in result.stdout


def test_bootout_timeout_stops_before_bootstrap(installer_env: dict[str, str]) -> None:
    _set_state(installer_env, "registered_running", "absent")
    installer_env["BOOTOUT_STICKY_LABEL"] = WHOOSHD_LABEL

    result = _run(installer_env)
    events = _events(installer_env)

    assert result.returncode != 0
    assert "Removal verification timed out" in result.stderr
    assert not any(" bootstrap " in event for event in events)


def test_bootout_indeterminate_state_fails_closed(installer_env: dict[str, str]) -> None:
    _set_state(installer_env, "registered_running", "absent")
    installer_env["BOOTOUT_INDETERMINATE_LABEL"] = WHOOSHD_LABEL

    result = _run(installer_env)

    assert result.returncode != 0
    assert "Removal verification indeterminate" in result.stderr


def test_bootstrap_success_requires_registered_state(installer_env: dict[str, str]) -> None:
    result = _run(installer_env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Bootstrap confirmed") == 2


def test_bootstrap_nonzero_registered_is_reconciled(installer_env: dict[str, str]) -> None:
    installer_env["BOOTSTRAP_NONZERO_REGISTERED_LABEL"] = WHOOSHD_LABEL

    result = _run(installer_env)

    assert result.returncode == 0, result.stderr
    assert "Bootstrap nonzero reconciled by registered state" in result.stdout
    assert "launchctl interpretation: Input/output error" in result.stdout


def test_bootstrap_nonzero_absent_reports_partial_state(installer_env: dict[str, str]) -> None:
    installer_env["BOOTSTRAP_NONZERO_ABSENT_LABEL"] = WHOOSHD_LABEL

    result = _run(installer_env)

    assert result.returncode != 0
    assert "post_state=absent" in result.stderr
    assert f"{WHOOSHD_LABEL}=absent" in result.stderr
    assert "Safe recovery:" in result.stderr
    assert "Recovery command: sudo -v && OUTPUT_DIR=" in result.stderr


def test_bootstrap_zero_absent_is_failure(installer_env: dict[str, str]) -> None:
    installer_env["BOOTSTRAP_ZERO_ABSENT_LABEL"] = WHOOSHD_LABEL

    result = _run(installer_env)

    assert result.returncode != 0
    assert "command_status=0 post_state=absent" in result.stderr


def test_second_bootstrap_failure_never_kickstarts_partial_pair(
    installer_env: dict[str, str],
) -> None:
    installer_env["BOOTSTRAP_NONZERO_ABSENT_LABEL"] = SIDECAR_LABEL

    result = _run(installer_env)
    events = _events(installer_env)

    assert result.returncode != 0
    assert f"{WHOOSHD_LABEL}=registered-not-running" in result.stderr
    assert f"{SIDECAR_LABEL}=absent" in result.stderr
    assert not any(" kickstart " in event for event in events)


def test_concurrent_installer_lock_is_rejected(installer_env: dict[str, str]) -> None:
    lock_dir = Path(installer_env["INSTALL_LOCK_DIR"])
    lock_dir.mkdir()

    result = _run(installer_env)

    assert result.returncode != 0
    assert "Another launchd bundle installer is active" in result.stderr
    assert not _events(installer_env)


def test_preflight_failure_occurs_before_privileged_mutation(
    installer_env: dict[str, str],
) -> None:
    plist_path = Path(installer_env["OUTPUT_DIR"]) / f"{WHOOSHD_LABEL}.plist"
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["EnvironmentVariables"]["WHOOSHD_PYTHON"] = "/missing/python"
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)

    result = _run(installer_env)

    assert result.returncode != 0
    assert "Whoosh'd Python preflight failed" in result.stderr
    assert not _events(installer_env)
    assert not Path(installer_env["INSTALL_LOCK_DIR"]).exists()


def test_existing_target_metadata_fails_before_privileged_mutation(
    installer_env: dict[str, str],
) -> None:
    target = Path(installer_env["SYSTEM_DIR"]) / f"{WHOOSHD_LABEL}.plist"
    target.write_text("existing target")
    installer_env["FAKE_STAT_OUTPUT"] = "local-user:staff:600"

    result = _run(installer_env)

    assert result.returncode != 0
    assert "expected root:wheel:644" in result.stderr
    assert not _events(installer_env)
    assert not Path(installer_env["INSTALL_LOCK_DIR"]).exists()


def test_wildcard_rendered_configuration_fails_before_mutation(
    installer_env: dict[str, str],
) -> None:
    plist_path = Path(installer_env["OUTPUT_DIR"]) / f"{WHOOSHD_LABEL}.plist"
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["EnvironmentVariables"]["WHOOSHD_HOST"] = "0.0.0.0"
    payload["ProgramArguments"][2] = "0.0.0.0"
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)

    result = _run(installer_env)

    assert result.returncode != 0
    assert "not the required loopback" in result.stderr
    assert not _events(installer_env)


def test_dry_run_is_mutation_and_query_free(installer_env: dict[str, str]) -> None:
    result = _run(installer_env, "dry-run")

    assert result.returncode == 0, result.stderr
    assert "No lock, privileged command, or launchd query was executed" in result.stdout
    assert not _events(installer_env)
    assert not Path(installer_env["INSTALL_LOCK_DIR"]).exists()


def test_initial_indeterminate_state_refuses_privileged_mutation(
    installer_env: dict[str, str],
) -> None:
    _set_state(installer_env, "indeterminate", "absent")

    result = _run(installer_env)
    events = _events(installer_env)

    assert result.returncode != 0
    assert "initial launchd state is indeterminate" in result.stderr
    assert not any(event.startswith("sudo ") for event in events)

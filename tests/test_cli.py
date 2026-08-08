"""Tests for the Whoosh'd CLI entrypoints."""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

import pytest

from whooshd import cli


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "process_matches_launch_nonce", lambda pid, nonce: True)
    return tmp_path


def test_help_output_is_product_shaped(capsys):
    with pytest.raises(SystemExit) as whoosh_exit:
        cli.main(["--help"])
    assert whoosh_exit.value.code == 0
    whoosh_help = capsys.readouterr().out

    with pytest.raises(SystemExit) as whooshd_exit:
        monkeypatch_prog = pytest.MonkeyPatch()
        monkeypatch_prog.setattr(sys, "argv", ["whooshd", "--help"])
        try:
            cli.main()
        finally:
            monkeypatch_prog.undo()
    assert whooshd_exit.value.code == 0
    whooshd_help = capsys.readouterr().out

    for help_text in (whoosh_help, whooshd_help):
        assert "Whoosh'd local inference broker" in help_text
        assert "whoosh -d" in help_text
        assert "whoosh down" in help_text
        assert "whoosh status" in help_text
        assert "whoosh logs" in help_text
        assert "Usage: python -m uvicorn" not in help_text


def test_start_aliases_route_to_single_start_path(monkeypatch):
    calls: list[tuple[str, argparse.Namespace]] = []

    def fake_start(args):
        calls.append(("start", args))
        return 0

    monkeypatch.setattr(cli, "start_server", fake_start)

    assert cli.main(["-d", "--no-wait"]) == 0
    assert cli.main(["up", "--no-wait"]) == 0

    monkeypatch.setattr(sys, "argv", ["whooshd", "up", "--no-wait"])
    assert cli.main() == 0

    assert cli.up_main(["--no-wait"]) == 0
    assert [name for name, _ in calls] == ["start", "start", "start", "start"]


def test_stop_aliases_route_to_single_stop_path(monkeypatch):
    calls: list[tuple[str, argparse.Namespace]] = []

    def fake_stop(args):
        calls.append(("stop", args))
        return 0

    monkeypatch.setattr(cli, "stop_server", fake_stop)

    assert cli.main(["down"]) == 0

    monkeypatch.setattr(sys, "argv", ["whooshd", "down"])
    assert cli.main() == 0

    assert cli.down_main([]) == 0
    assert [name for name, _ in calls] == ["stop", "stop", "stop"]


def test_start_command_constructs_uvicorn_and_writes_pid(isolated_home, monkeypatch):
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4242

    def fake_launch(command, env):
        captured["command"] = command
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr(cli, "is_port_occupied", lambda host, port: False)
    monkeypatch.setattr(cli, "launch_server", fake_launch)
    monkeypatch.setattr(cli, "wait_until_reachable", lambda host, port, timeout: True)

    assert cli.main(["-d"]) == 0

    assert captured["command"] == [
        sys.executable,
        "-m",
        "uvicorn",
        "whooshd.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    env = captured["env"]
    assert env["WHOOSHD_MLX_ENABLED"] == "true"
    assert env["WHOOSHD_MLX_MODEL"] == cli.DEFAULT_MLX_MODEL
    assert (isolated_home / ".whooshd" / "whooshd.pid").read_text() == "4242\n"


def test_shutdown_reads_tracked_pid(isolated_home, monkeypatch):
    (isolated_home / ".whooshd").mkdir()
    (isolated_home / ".whooshd" / "whooshd.pid").write_text("4242\n")
    signaled_groups: list[tuple[int, int]] = []

    def fake_group_alive(pid):
        return len(signaled_groups) == 0

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", fake_group_alive)
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: signaled_groups.append((pid, sig)))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)

    assert cli.main(["down", "--timeout", "0.01"]) == 0
    assert signaled_groups == [(4242, cli.signal.SIGTERM)]
    assert not (isolated_home / ".whooshd" / "whooshd.pid").exists()


def test_stale_pid_file_is_handled_safely(isolated_home, monkeypatch, capsys):
    (isolated_home / ".whooshd").mkdir()
    (isolated_home / ".whooshd" / "whooshd.pid").write_text("9999\n")

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(cli, "is_port_occupied", lambda host, port: False)
    monkeypatch.setattr(cli, "launch_server", lambda command, env: SimpleNamespace(pid=1010))
    monkeypatch.setattr(cli, "wait_until_reachable", lambda host, port, timeout: True)

    assert cli.main(["up"]) == 0
    assert "Removing stale Whoosh'd PID file" in capsys.readouterr().out
    assert (isolated_home / ".whooshd" / "whooshd.pid").read_text() == "1010\n"


def test_stop_refuses_unverified_live_pid(isolated_home, monkeypatch, capsys):
    (isolated_home / ".whooshd").mkdir()
    (isolated_home / ".whooshd" / "whooshd.pid").write_text("4242\n")
    monkeypatch.setattr(cli, "process_matches_launch_nonce", lambda pid, nonce: False)
    monkeypatch.setattr(cli, "process_matches_legacy_daemon", lambda pid: False)
    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)

    assert cli.main(["down"]) == 2
    assert "refusing to signal" in capsys.readouterr().err
    assert (isolated_home / ".whooshd" / "whooshd.pid").exists()


def test_nonce_verification_accepts_surviving_group_member(monkeypatch):
    monkeypatch.setattr(cli, "_process_group_member_pids", lambda pgid: ["4243"])
    monkeypatch.setattr(
        cli,
        "_process_command",
        lambda pid, *, include_environment=False: (
            "WHOOSHD_LAUNCH_NONCE=nonce-123 "
            f"{sys.executable} -m uvicorn whooshd.app:app"
            if include_environment
            else f"{sys.executable} -m uvicorn whooshd.app:app"
        ),
    )

    assert cli.process_matches_launch_nonce(4242, "nonce-123")


def test_nonce_verification_rejects_environment_only_command_spoof(monkeypatch):
    monkeypatch.setattr(cli, "_process_group_member_pids", lambda pgid: ["4243"])
    monkeypatch.setattr(
        cli,
        "_process_command",
        lambda pid, *, include_environment=False: (
            "WHOOSHD_LAUNCH_NONCE=nonce-123 "
            "python -m uvicorn whooshd.app:app"
            if include_environment
            else "python unrelated_service.py"
        ),
    )

    assert not cli.process_matches_launch_nonce(4242, "nonce-123")


def test_legacy_verification_requires_whooshd_group_member(monkeypatch):
    monkeypatch.setattr(cli, "_process_group_member_pids", lambda pgid: ["4243", "4244"])
    monkeypatch.setattr(
        cli,
        "_process_command",
        lambda pid, *, include_environment=False: (
            f"{sys.executable} -m uvicorn whooshd.app:app"
            if pid == "4243"
            else "python unrelated_service.py"
        ),
    )
    assert cli.process_matches_legacy_daemon(4242)

    monkeypatch.setattr(cli, "_process_group_member_pids", lambda pgid: ["4244"])
    monkeypatch.setattr(
        cli,
        "_process_command",
        lambda pid, *, include_environment=False: "python unrelated_service.py",
    )
    assert not cli.process_matches_legacy_daemon(4242)


def test_start_preserves_verified_legacy_group_after_leader_exit(
    isolated_home, monkeypatch, capsys
):
    state = isolated_home / ".whooshd"
    state.mkdir()
    (state / "whooshd.pid").write_text("4242\n")

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "process_matches_launch_nonce", lambda pid, nonce: False)
    monkeypatch.setattr(cli, "process_matches_legacy_daemon", lambda pid: True)
    monkeypatch.setattr(
        cli,
        "launch_server",
        lambda command, env: pytest.fail("must not start a second daemon"),
    )

    assert cli.main(["up"]) == 0
    assert "tracked process group 4242" in capsys.readouterr().out


def test_force_stop_recovers_verified_legacy_group_after_leader_exit(
    isolated_home, monkeypatch
):
    pid_file = isolated_home / ".whooshd" / "whooshd.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("4242\n")
    signaled_groups: list[tuple[int, int]] = []
    wait_results = iter([False, True])

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "process_matches_launch_nonce", lambda pid, nonce: False)
    monkeypatch.setattr(cli, "process_matches_legacy_daemon", lambda pid: True)
    monkeypatch.setattr(
        cli,
        "signal_process_group",
        lambda pid, sig: signaled_groups.append((pid, sig)) or True,
    )
    monkeypatch.setattr(
        cli,
        "wait_for_process_group_exit",
        lambda pid, timeout: next(wait_results),
    )

    assert cli.main(["down", "--force"]) == 0
    assert signaled_groups == [
        (4242, cli.signal.SIGTERM),
        (4242, cli.signal.SIGKILL),
    ]
    assert not pid_file.exists()


def test_unknown_process_on_port_is_not_killed(isolated_home, monkeypatch, capsys):
    signaled_groups: list[tuple[int, int]] = []

    monkeypatch.setattr(cli, "is_port_occupied", lambda host, port: True)
    monkeypatch.setattr(
        cli.os,
        "killpg",
        lambda pid, sig: signaled_groups.append((pid, sig)),
    )

    assert cli.main(["up"]) == 2
    assert signaled_groups == []
    stderr = capsys.readouterr().err
    assert "already in use" in stderr
    assert "lsof -nP -iTCP:8000 -sTCP:LISTEN" in stderr


def test_status_reports_stale_tracking(isolated_home, monkeypatch, capsys):
    (isolated_home / ".whooshd").mkdir()
    (isolated_home / ".whooshd" / "whooshd.pid").write_text("4242\n")

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: False)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: False)
    monkeypatch.setattr(cli, "fetch_json", lambda host, port, path: (None, "offline"))

    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "Tracked PID: 4242" in out
    assert "Process alive: no" in out
    assert "Process group alive: no" in out
    assert "Tracking state: stale" in out


def test_status_reports_probe_states(isolated_home, monkeypatch, capsys):
    (isolated_home / ".whooshd").mkdir()
    (isolated_home / ".whooshd" / "whooshd.pid").write_text("4242\n")

    responses = {
        "/health": (200, '{"ok": true}'),
        "/ready": (503, '{"ready": false}'),
        "/v1/models": (200, '{"data": []}'),
    }
    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "fetch_json", lambda host, port, path: responses[path])

    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "Tracked PID: 4242" in out
    assert "Process alive: yes" in out
    assert "Process group alive: yes" in out
    assert "health: HTTP 200" in out
    assert "ready: HTTP 503" in out
    assert "v1/models: HTTP 200" in out


def test_stop_timeout_without_force_leaves_pid_file(isolated_home, monkeypatch, capsys):
    pid_file = isolated_home / ".whooshd" / "whooshd.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("4242\n")
    signaled_groups: list[tuple[int, int]] = []

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "signal_process_group", lambda pid, sig: signaled_groups.append((pid, sig)) or True)
    monkeypatch.setattr(cli, "wait_for_process_group_exit", lambda pid, timeout: False)

    assert cli.main(["down", "--timeout", "0.01"]) == 1
    assert signaled_groups == [(4242, cli.signal.SIGTERM)]
    assert pid_file.read_text() == "4242\n"
    stderr = capsys.readouterr().err
    assert "did not stop" in stderr
    assert "Leaving" in stderr


def test_force_stop_sends_sigkill_after_timeout(isolated_home, monkeypatch):
    pid_file = isolated_home / ".whooshd" / "whooshd.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("4242\n")
    signaled_groups: list[tuple[int, int]] = []
    wait_results = iter([False, True])

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "signal_process_group", lambda pid, sig: signaled_groups.append((pid, sig)) or True)
    monkeypatch.setattr(cli, "wait_for_process_group_exit", lambda pid, timeout: next(wait_results))

    assert cli.main(["down", "--timeout", "0.01", "--force"]) == 0
    assert signaled_groups == [
        (4242, cli.signal.SIGTERM),
        (4242, cli.signal.SIGKILL),
    ]
    assert not pid_file.exists()


def test_force_stop_leaves_pid_file_if_sigkill_does_not_end_group(isolated_home, monkeypatch):
    pid_file = isolated_home / ".whooshd" / "whooshd.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("4242\n")

    monkeypatch.setattr(cli, "is_process_alive", lambda pid: True)
    monkeypatch.setattr(cli, "is_process_group_alive", lambda pid: True)
    monkeypatch.setattr(cli, "signal_process_group", lambda pid, sig: True)
    monkeypatch.setattr(cli, "wait_for_process_group_exit", lambda pid, timeout: False)

    assert cli.main(["down", "--force"]) == 1
    assert pid_file.read_text() == "4242\n"


def test_reload_command_construction_preserves_daemon_owner(isolated_home, monkeypatch):
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    process = cli.launch_server(
        cli.build_uvicorn_command("127.0.0.1", 8000, reload=True),
        {"WHOOSHD_MLX_ENABLED": "true"},
    )

    assert process.pid == 4242
    assert captured["command"] == [
        sys.executable,
        "-m",
        "uvicorn",
        "whooshd.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--reload",
    ]
    assert captured["kwargs"]["start_new_session"] is True


def test_logs_print_tail(isolated_home, capsys):
    log_dir = isolated_home / ".whooshd"
    log_dir.mkdir()
    (log_dir / "whooshd.log").write_text("\n".join(f"line {i}" for i in range(20)))

    assert cli.main(["logs", "--tail", "3"]) == 0
    assert capsys.readouterr().out.splitlines() == ["line 17", "line 18", "line 19"]

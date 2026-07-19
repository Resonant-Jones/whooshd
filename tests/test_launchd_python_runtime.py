from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO_ROOT / "ops" / "launchd"
RENDERER = LAUNCHD_DIR / "render_launchd_plists.py"
INSTALLER = LAUNCHD_DIR / "install_local_launchd.sh"

sys.path.insert(0, str(LAUNCHD_DIR))
from validate_whooshd_python import PythonPreflightError, validate_whooshd_python  # noqa: E402


def _make_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _renderer_command(tmp_path: Path, *, include_python: bool = True) -> list[str]:
    launcher = _make_executable(tmp_path / "whooshd-launcher", "#!/bin/sh\nexit 0\n")
    model_path = tmp_path / "model"
    model_path.mkdir()
    command = [
        sys.executable,
        str(RENDERER),
        "--output-dir",
        str(tmp_path / "rendered"),
        "--whooshd-root",
        str(REPO_ROOT),
        "--user",
        "test-user",
        "--whooshd-launcher",
        str(launcher),
        "--mlx-vlm-python",
        sys.executable,
        "--mlx-vlm-model-path",
        str(model_path),
        "--dry-run",
    ]
    if include_python:
        command.extend(["--whooshd-python", sys.executable])
    return command


def test_renderer_requires_explicit_whooshd_python(tmp_path: Path) -> None:
    fake_implicit = _make_executable(
        tmp_path / ".venv" / "bin" / "python",
        "#!/bin/sh\nexit 0\n",
    )
    assert fake_implicit.exists()

    result = subprocess.run(
        _renderer_command(tmp_path, include_python=False),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--whooshd-python" in result.stderr


def test_valid_interpreter_is_rendered_without_changing_sidecar_shape(tmp_path: Path) -> None:
    result = subprocess.run(
        _renderer_command(tmp_path),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Suggested convergent install command:" in result.stdout
    assert "install_local_launchd.sh" in result.stdout
    assert "sudo launchctl" not in result.stdout

    output_dir = tmp_path / "rendered"
    with (output_dir / "com.resonant.whooshd.plist").open("rb") as handle:
        whooshd = plistlib.load(handle)
    with (output_dir / "com.resonant.mlx-vlm-gemma12b.plist").open("rb") as handle:
        sidecar = plistlib.load(handle)

    assert whooshd["ProgramArguments"][-4:] == ["--host", "127.0.0.1", "--port", "8000"]
    assert whooshd["EnvironmentVariables"]["WHOOSHD_HOST"] == "127.0.0.1"
    assert whooshd["EnvironmentVariables"]["WHOOSHD_PYTHON"] == sys.executable
    assert sidecar == {
        "Label": "com.resonant.mlx-vlm-gemma12b",
        "UserName": "test-user",
        "ProgramArguments": [
            sys.executable,
            "-m",
            "mlx_vlm",
            "server",
            "--model",
            str(tmp_path / "model"),
            "--host",
            "127.0.0.1",
            "--port",
            "8082",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO_ROOT),
        "StandardOutPath": "/tmp/mlx-vlm-gemma12b.out",
        "StandardErrorPath": "/tmp/mlx-vlm-gemma12b.err",
        "EnvironmentVariables": {
            "PATH": (
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:"
                "/Users/chriscastillo/.local/bin"
            ),
            "PYTHONUNBUFFERED": "1",
        },
    }


def test_missing_interpreter_fails() -> None:
    with pytest.raises(PythonPreflightError, match="not found"):
        validate_whooshd_python("/missing/whooshd-python", REPO_ROOT)


def test_non_executable_interpreter_fails(tmp_path: Path) -> None:
    interpreter = tmp_path / "python"
    interpreter.write_text("not executable")

    with pytest.raises(PythonPreflightError, match="not executable"):
        validate_whooshd_python(interpreter, REPO_ROOT)


def test_required_import_failure_fails_closed(tmp_path: Path) -> None:
    interpreter = _make_executable(
        tmp_path / "python",
        "#!/bin/sh\necho 'simulated required import failure' >&2\nexit 23\n",
    )

    with pytest.raises(PythonPreflightError, match="required-import preflight failed"):
        validate_whooshd_python(interpreter, REPO_ROOT)


def test_installer_preflight_precedes_any_service_mutation() -> None:
    source = INSTALLER.read_text()
    preflight = source.index('validate_whooshd_python.py" --plist')
    lock = source.rindex("\nacquire_lock\n")
    first_mutation = source.index('\n  "$SUDO_BIN" cp "$WHOOSHD_TARGET"')
    first_bootout = source.index('\nif ! remove_if_registered "$WHOOSHD_LABEL"')

    assert preflight < lock
    assert preflight < first_mutation
    assert preflight < first_bootout


@pytest.mark.skipif(shutil.which("plutil") is None, reason="launchd installer requires plutil")
def test_invalid_rendered_interpreter_cannot_reach_sudo(tmp_path: Path) -> None:
    output_dir = tmp_path / "rendered"
    output_dir.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    sentinel = tmp_path / "sudo-called"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _make_executable(
        fake_bin / "sudo",
        f"#!/bin/sh\nprintf called > {sentinel}\nexit 99\n",
    )

    with (output_dir / "com.resonant.whooshd.plist").open("wb") as handle:
        plistlib.dump(
            {
                "Label": "com.resonant.whooshd",
                "EnvironmentVariables": {
                    "WHOOSHD_PYTHON": str(tmp_path / "missing-python"),
                    "WHOOSHD_ROOT": str(root),
                },
            },
            handle,
        )
    with (output_dir / "com.resonant.mlx-vlm-gemma12b.plist").open("wb") as handle:
        plistlib.dump({"Label": "com.resonant.mlx-vlm-gemma12b"}, handle)

    env = os.environ.copy()
    env["OUTPUT_DIR"] = str(output_dir)
    env["PATH"] = os.pathsep.join(
        [str(fake_bin), str(Path(sys.executable).parent), env.get("PATH", "")]
    )
    result = subprocess.run(
        ["bash", str(INSTALLER), "install"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Whoosh'd Python preflight failed" in result.stderr
    assert not sentinel.exists()

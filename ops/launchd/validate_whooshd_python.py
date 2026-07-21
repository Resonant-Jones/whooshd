#!/usr/bin/env python3
"""Validate the explicit Python interpreter used by the Whoosh'd LaunchDaemon."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys


PREFLIGHT_MARKER = "WHOOSHD_PYTHON_PREFLIGHT_OK"
PREFLIGHT_CODE = f"""
import importlib
import sys

for module_name in (
    "fastapi",
    "uvicorn",
    "pydantic_core._pydantic_core",
    "whooshd.app",
):
    importlib.import_module(module_name)

print("{PREFLIGHT_MARKER}:" + sys.executable)
"""


class PythonPreflightError(ValueError):
    """Raised when the selected Whoosh'd interpreter cannot start the app."""


def _absolute_path(raw_path: str | os.PathLike[str], label: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise PythonPreflightError(f"{label} must be an absolute path: {candidate}")
    return Path(os.path.normpath(str(candidate)))


def validate_whooshd_python(
    python_path: str | os.PathLike[str],
    whooshd_root: str | os.PathLike[str],
    *,
    timeout_seconds: float = 30.0,
) -> Path:
    """Return the validated interpreter path or fail without selecting a fallback."""

    interpreter = _absolute_path(python_path, "Whoosh'd Python")
    root = _absolute_path(whooshd_root, "Whoosh'd root")

    if not interpreter.is_file():
        raise PythonPreflightError(f"Whoosh'd Python not found: {interpreter}")
    if not os.access(interpreter, os.X_OK):
        raise PythonPreflightError(f"Whoosh'd Python is not executable: {interpreter}")
    if not root.is_dir():
        raise PythonPreflightError(f"Whoosh'd root not found: {root}")

    try:
        result = subprocess.run(
            [str(interpreter), "-c", PREFLIGHT_CODE],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        raise PythonPreflightError(
            f"Whoosh'd Python could not execute: {interpreter}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PythonPreflightError(
            f"Whoosh'd Python preflight timed out after {timeout_seconds:g}s: {interpreter}"
        ) from exc

    if result.returncode != 0:
        detail_lines = (result.stderr or result.stdout).strip().splitlines()
        detail = detail_lines[-1] if detail_lines else f"exit {result.returncode}"
        raise PythonPreflightError(
            f"Whoosh'd Python required-import preflight failed: {interpreter}: {detail}"
        )
    if PREFLIGHT_MARKER not in result.stdout:
        raise PythonPreflightError(
            f"Whoosh'd Python did not return the interpreter preflight marker: {interpreter}"
        )

    return interpreter


def selection_from_plist(plist_path: str | os.PathLike[str]) -> tuple[str, str]:
    path = Path(plist_path)
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
        environment = payload["EnvironmentVariables"]
        python_path = environment["WHOOSHD_PYTHON"]
        whooshd_root = environment["WHOOSHD_ROOT"]
    except (OSError, KeyError, TypeError, plistlib.InvalidFileException) as exc:
        raise PythonPreflightError(
            f"Whoosh'd plist is missing a valid explicit Python selection: {path}: {exc}"
        ) from exc

    if not isinstance(python_path, str) or not isinstance(whooshd_root, str):
        raise PythonPreflightError(
            f"Whoosh'd plist Python and root values must be strings: {path}"
        )
    return python_path, whooshd_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plist", help="Rendered Whoosh'd plist to validate.")
    parser.add_argument("--python", dest="python_path", help="Explicit Whoosh'd Python path.")
    parser.add_argument("--whooshd-root", help="Whoosh'd repository root.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.plist:
            if args.python_path or args.whooshd_root:
                raise PythonPreflightError(
                    "--plist cannot be combined with --python or --whooshd-root"
                )
            python_path, whooshd_root = selection_from_plist(args.plist)
        else:
            if not args.python_path or not args.whooshd_root:
                raise PythonPreflightError(
                    "provide --plist or both --python and --whooshd-root"
                )
            python_path = args.python_path
            whooshd_root = args.whooshd_root

        validated = validate_whooshd_python(python_path, whooshd_root)
    except PythonPreflightError as exc:
        print(f"Whoosh'd Python preflight failed: {exc}", file=sys.stderr)
        return 1

    print(f"Validated Whoosh'd Python: {validated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

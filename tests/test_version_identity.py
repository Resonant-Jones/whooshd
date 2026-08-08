"""Regression tests for the release version identity contract."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from whooshd import __version__
from whooshd.app import app, health
from whooshd.routing import inventory_provenance


def _project_version() -> str:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text())
    return project["project"]["version"]


@pytest.mark.asyncio
async def test_runtime_version_matches_pyproject_across_public_surfaces():
    expected = _project_version()

    assert __version__ == expected
    assert app.version == expected
    assert (await health()).version == expected
    assert inventory_provenance(
        model_id="stub-model",
        runtime_kind="stub",
        resolution_source="configured_stub",
        loaded=True,
    ).whooshd_version == expected

    changelog = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text()
    assert f"## v{expected} (Unreleased)" in changelog

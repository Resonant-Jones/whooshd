"""Model registry bootstrap — creates the persistent model-store layout.

The bootstrap function is a pure filesystem operation.  It does not:
  - scan for models
  - register models
  - download anything
  - modify existing model files
  - touch the runtime adapter layer
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Union

from whooshd.model_registry.contracts import (
    ModelRegistryManifest,
    ModelRegistryState,
    ModelStoreLayout,
)
from whooshd.log_safety import exception_metadata


def bootstrap_model_store(
    store_root: Union[str, Path],
) -> ModelRegistryState:
    """Create or validate the Whoosh'd model-store directory layout.

    Args:
        store_root: Path to the model-store root.  ``~`` is expanded
                    and the result resolved to an absolute path.

    Returns:
        A ``ModelRegistryState`` describing what was created or reused.

    Raises:
        ValueError: if *store_root* is empty, resolves to ``/``, or
                    cannot be created.

    Safety guarantees:
      - Never deletes existing files.
      - Never modifies files outside the store root.
      - Writes new manifests atomically through a temp file in the
        store's ``tmp/`` directory.
      - Preserves an existing ``registry/models.json`` without
        overwriting it.
      - Validates that an existing manifest has a supported
        ``schema_version``.
    """
    # ── Validate input ────────────────────────────────────────────────
    raw = str(store_root).strip()
    if not raw:
        raise ValueError("store_root must not be empty")

    expanded = Path(os.path.expanduser(raw)).resolve()

    if expanded == Path("/"):
        raise ValueError("store_root must not resolve to the filesystem root (/)")

    # ── Create directories ────────────────────────────────────────────
    layout = ModelStoreLayout(store_root=expanded)
    dirs_created: list[str] = []

    for directory in layout.all_directories():
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            dirs_created.append(str(directory))

    # ── Handle manifest ───────────────────────────────────────────────
    manifest_created = False
    manifest_reused = False
    manifest = None

    if layout.manifest_path.exists():
        # Existing manifest — validate, do not overwrite.
        manifest_reused = True
        try:
            raw_data = layout.manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw_data)
        except (json.JSONDecodeError, OSError) as exc:
            return ModelRegistryState(
                store_root=str(expanded),
                manifest_path=str(layout.manifest_path),
                directories_created=dirs_created,
                error=f"Existing manifest is unreadable ({exception_metadata(exc)})",
            )

        manifest = ModelRegistryManifest.from_dict(data)

        if manifest.schema_version != 1:
            return ModelRegistryState(
                store_root=str(expanded),
                manifest_path=str(layout.manifest_path),
                directories_created=dirs_created,
                error=(
                    f"Unsupported manifest schema_version={manifest.schema_version}. "
                    f"Expected 1."
                ),
            )

        # Touch the timestamp.
        manifest.touch()
        _write_manifest_atomic(manifest, layout)

    else:
        # No manifest — create one.
        manifest_created = True
        manifest = ModelRegistryManifest.create(expanded)
        _write_manifest_atomic(manifest, layout)

    assert manifest is not None

    return ModelRegistryState(
        store_root=str(expanded),
        manifest_path=str(layout.manifest_path),
        manifest_created=manifest_created,
        manifest_reused=manifest_reused,
        directories_created=dirs_created,
        schema_version=manifest.schema_version,
    )


def _write_manifest_atomic(
    manifest: ModelRegistryManifest,
    layout: ModelStoreLayout,
) -> None:
    """Write the manifest to ``registry/models.json`` atomically.

    Uses a temp file in ``tmp/`` and renames it into place so a partial
    write never corrupts the manifest.
    """
    payload = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False)

    # Ensure tmp/ exists.
    layout.tmp.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in tmp/, then rename into registry/.
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix="manifest-", dir=str(layout.tmp)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(layout.manifest_path))
    except Exception:
        # Clean up the temp file on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

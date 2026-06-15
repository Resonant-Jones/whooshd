"""Model registry inventory — collect advertisable registered models.

Read-only helper that scans the durable model-store for compatible
registered models that are ready for runtime advertisement.  Does not
launch adapters, modify files, or change runtime state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from whooshd.model_registry.compatibility import (
    validate_registered_model_compatibility,
)
from whooshd.model_registry.contracts import (
    ModelRegistryManifest,
    ModelStoreLayout,
    RegisteredModel,
    RegisteredModelCompatibilityStatus,
    RegisteredModelStorageMode,
)


def collect_advertisable_registered_models(
    store_root: Union[str, Path],
) -> list[RegisteredModel]:
    """Collect registered models that are compatible and advertisable.

    Iterates ``registry/models.json``, runs compatibility validation for
    each entry, and returns only those with ``advertisable=true``.

    Args:
        store_root: Path to a bootstrapped model-store.

    Returns:
        List of ``RegisteredModel`` entries that are safe to advertise.
        Returns an empty list if the store is missing, unreadable, or
        has an unsupported schema version.

    Safety:
      - Read-only.  Never mutates manifests, candidates, or model files.
      - Never launches adapters.
      - Returns empty list on any error rather than raising.
    """
    root = Path(store_root).resolve()
    layout = ModelStoreLayout(store_root=root)

    if not layout.registry_dir.exists() or not layout.manifest_path.exists():
        return []

    try:
        data = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    manifest = ModelRegistryManifest.from_dict(data)
    if manifest.schema_version != 1:
        return []

    results: list[RegisteredModel] = []
    seen_ids: set[str] = set()

    for entry in manifest.models:
        model_id = str(entry.get("model_id", ""))
        if not model_id or model_id in seen_ids:
            continue
        seen_ids.add(model_id)

        rm = RegisteredModel.from_dict(entry)
        if rm.status != "registered":
            continue
        if rm.storage_mode != RegisteredModelStorageMode.MANAGED:
            continue

        # Run compatibility validation.
        compat = validate_registered_model_compatibility(str(root), model_id)
        if compat.advertisable:
            results.append(rm)

    # Stable ordering: sort by model_id.
    results.sort(key=lambda m: m.model_id)
    return results

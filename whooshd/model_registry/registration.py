"""Model registry registration — promote candidates into managed models.

Copies inspected candidate artifacts into the managed model-store and
appends registered model entries to ``registry/models.json``.

Registered models are *not* advertised in ``/v1/models`` or runnable.
Those are separate lifecycle transitions handled by the runtime layer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from whooshd.model_registry.contracts import (
    ModelCandidate,
    ModelCandidateFormat,
    ModelCandidateStatus,
    ModelRegistryManifest,
    ModelRegistrationResult,
    ModelStoreLayout,
    RegisteredModel,
    RegisteredModelStatus,
    RegisteredModelStorageMode,
)


# ── Safe model ID pattern ──────────────────────────────────────────────────

_SAFE_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _validate_model_id(model_id: str) -> str | None:
    """Return a problem code if *model_id* is unsafe, or None if valid."""
    if not model_id or not model_id.strip():
        return "unsafe_model_id"
    if not _SAFE_MODEL_ID_RE.match(model_id):
        return "unsafe_model_id"
    if ".." in model_id or model_id.startswith("/") or model_id.startswith("."):
        return "unsafe_model_id"
    return None


# ── Registration ───────────────────────────────────────────────────────────


def register_model_candidate(
    store_root: Union[str, Path],
    candidate_id: str,
    model_id: str | None = None,
    display_name: str | None = None,
) -> ModelRegistrationResult:
    """Promote an inspected candidate into the managed model registry.

    1. Loads the candidate record from ``registry/candidates/``.
    2. Validates the candidate is registrable.
    3. Copies the source artifact into the managed store.
    4. Appends a ``RegisteredModel`` entry to ``registry/models.json``.

    Args:
        store_root: Path to a bootstrapped model-store.
        candidate_id: The candidate to register.
        model_id: Optional stable model identifier.  Derived if omitted.
        display_name: Optional human-readable name.  Derived if omitted.

    Returns:
        ``ModelRegistrationResult`` with the registered model and any problem.
    """
    layout = ModelStoreLayout(store_root=Path(store_root).resolve())

    # ── Validate store is bootstrapped ────────────────────────────────
    if not layout.registry_dir.exists():
        return _fail(
            RegisteredModel(model_id=model_id or candidate_id),
            layout,
            "store_not_bootstrapped",
        )

    # ── Load and validate manifest ────────────────────────────────────
    manifest_result = _load_manifest(layout)
    if isinstance(manifest_result, ModelRegistrationResult):
        return manifest_result
    manifest = manifest_result

    # ── Load candidate ────────────────────────────────────────────────
    candidate_path = layout.registry_dir / "candidates" / f"{_safe_filename(candidate_id)}.json"
    if not candidate_path.exists():
        return _fail(
            RegisteredModel(model_id=model_id or candidate_id),
            layout, "candidate_missing",
        )

    try:
        candidate = ModelCandidate.from_dict(
            json.loads(candidate_path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, OSError):
        return _fail(
            RegisteredModel(model_id=model_id or candidate_id),
            layout, "candidate_missing",
        )

    # ── Validate candidate is registrable ─────────────────────────────
    if candidate.status != ModelCandidateStatus.CANDIDATE:
        return _fail(
            RegisteredModel(model_id=model_id or candidate_id),
            layout, "candidate_not_registrable",
        )
    if candidate.detected_format == ModelCandidateFormat.UNKNOWN:
        return _fail(
            RegisteredModel(model_id=model_id or candidate_id),
            layout, "unsupported_format",
        )

    # ── Build registered model ────────────────────────────────────────
    if model_id is not None and not model_id.strip():
        return _fail(
            RegisteredModel(model_id=model_id),
            layout, "unsafe_model_id",
        )
    resolved_id = model_id.strip() if (model_id is not None and model_id.strip()) else _derive_model_id(candidate)
    problem = _validate_model_id(resolved_id)
    if problem:
        return _fail(RegisteredModel(model_id=resolved_id), layout, problem)

    resolved_display = display_name or candidate.source_path.rsplit("/", 1)[-1]
    now = datetime.now(timezone.utc).isoformat()

    # ── Determine managed destination ─────────────────────────────────
    dest_dir, managed_rel = _managed_destination(layout, candidate, resolved_id)
    dest_path = layout.store_root / managed_rel

    # ── Check for duplicate model_id ──────────────────────────────────
    existing = _find_existing(manifest, resolved_id)
    if existing is not None:
        if _models_match(existing, candidate, resolved_id):
            # Idempotent — return existing.
            return ModelRegistrationResult(
                registered_model=RegisteredModel.from_dict(existing),
                managed_path=str(managed_rel),
                manifest_updated=False,
            )
        return _fail(
            RegisteredModel(model_id=resolved_id),
            layout, "duplicate_model_id",
        )

    # ── Check destination ─────────────────────────────────────────────
    source = Path(candidate.source_path)
    if not source.exists():
        return _fail(
            RegisteredModel(model_id=resolved_id),
            layout, "candidate_missing",
        )

    if dest_path.exists():
        return _fail(
            RegisteredModel(model_id=resolved_id),
            layout, "managed_destination_exists",
        )

    # ── Copy ──────────────────────────────────────────────────────────
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest_path, symlinks=False, dirs_exist_ok=False)
        else:
            shutil.copy2(source, dest_path)
    except (OSError, shutil.Error) as exc:
        return _fail(
            RegisteredModel(model_id=resolved_id),
            layout, "copy_failed",
        )

    # ── Build registered model entry ──────────────────────────────────
    registered = RegisteredModel(
        model_id=resolved_id,
        display_name=resolved_display,
        status=RegisteredModelStatus.REGISTERED,
        storage_mode=RegisteredModelStorageMode.MANAGED,
        managed_path=str(managed_rel),
        source_candidate_id=candidate.candidate_id,
        source_path=candidate.source_path,
        detected_format=candidate.detected_format,
        detected_family=candidate.detected_family,
        modalities=list(candidate.modalities),
        evidence=list(candidate.evidence),
        created_at=now,
        updated_at=now,
    )

    # ── Update manifest atomically ────────────────────────────────────
    manifest.models.append(registered.to_dict())
    manifest.touch()
    _write_manifest_atomic(manifest, layout)

    return ModelRegistrationResult(
        registered_model=registered,
        managed_path=str(managed_rel),
        manifest_updated=True,
    )


# ── Managed destination logic ──────────────────────────────────────────────


def _managed_destination(
    layout: ModelStoreLayout,
    candidate: ModelCandidate,
    model_id: str,
) -> tuple[Path, Path]:
    """Return (parent_directory_for_mkdir, relative_managed_path).

    The parent directory is what ``mkdir -p`` should create.
    The managed path is the full relative path to the artifact.
    """
    safe_id = _safe_filename(model_id)
    fmt = candidate.detected_format
    is_vision = "vision" in candidate.modalities

    if fmt == ModelCandidateFormat.GGUF:
        src_name = Path(candidate.source_path).name
        parent = layout.store_root / "models" / "gguf" / safe_id
        rel = Path("models") / "gguf" / safe_id / src_name
        return (parent, rel)

    if is_vision:
        parent = layout.store_root / "models" / "vlm"
        rel = Path("models") / "vlm" / safe_id
        return (parent, rel)

    parent = layout.store_root / "models" / "mlx"
    rel = Path("models") / "mlx" / safe_id
    return (parent, rel)


# ── Manifest helpers ───────────────────────────────────────────────────────


def _load_manifest(
    layout: ModelStoreLayout,
) -> ModelRegistryManifest | ModelRegistrationResult:
    """Load and validate the manifest.  Returns manifest or error result."""
    if not layout.manifest_path.exists():
        return _fail(
            RegisteredModel(model_id=""),
            layout, "store_not_bootstrapped",
        )
    try:
        data = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _fail(
            RegisteredModel(model_id=""),
            layout, "manifest_unreadable",
        )
    manifest = ModelRegistryManifest.from_dict(data)
    if manifest.schema_version != 1:
        return _fail(
            RegisteredModel(model_id=""),
            layout, "manifest_schema_unsupported",
        )
    return manifest


def _find_existing(
    manifest: ModelRegistryManifest,
    model_id: str,
) -> dict | None:
    """Return an existing manifest entry for *model_id*, or None."""
    for entry in manifest.models:
        if entry.get("model_id") == model_id:
            return entry
    return None


def _models_match(
    existing: dict,
    candidate: ModelCandidate,
    model_id: str,
) -> bool:
    """Return True if the existing entry matches the candidate."""
    return (
        existing.get("model_id") == model_id
        and existing.get("source_candidate_id") == candidate.candidate_id
        and existing.get("detected_format") == candidate.detected_format
    )


def _derive_model_id(candidate: ModelCandidate) -> str:
    """Derive a safe default model_id from the candidate."""
    src_name = Path(candidate.source_path).name
    # Remove common suffixes.
    base = src_name.removesuffix(".gguf")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "-", base)[:64].strip("-.")
    if not safe:
        safe = f"model-{candidate.candidate_id[:8]}"
    return safe


def _write_manifest_atomic(
    manifest: ModelRegistryManifest,
    layout: ModelStoreLayout,
) -> None:
    """Write *manifest* to ``registry/models.json`` atomically."""
    payload = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False)
    layout.tmp.mkdir(parents=True, exist_ok=True)
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
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename component."""
    return "".join(c for c in name if c.isalnum() or c in "_-.")[:64]


def _fail(
    model: RegisteredModel,
    layout: ModelStoreLayout,
    problem: str,
) -> ModelRegistrationResult:
    """Return a failed registration result with the given problem code."""
    return ModelRegistrationResult(
        registered_model=model,
        problem=problem,
    )

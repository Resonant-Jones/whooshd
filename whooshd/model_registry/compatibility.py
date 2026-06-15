"""Model registry compatibility — validate registered models against adapters.

Read-only validator that inspects a registered model's metadata and managed
files to determine whether it is plausibly compatible with a Whoosh'd runtime
adapter.  Does NOT launch, warm, or call any adapter.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from whooshd.model_registry.contracts import (
    ModelCandidateFormat,
    ModelRegistryManifest,
    ModelStoreLayout,
    RegisteredModel,
    RegisteredModelAdapterKind,
    RegisteredModelCompatibilityResult,
    RegisteredModelCompatibilityStatus,
    RegisteredModelStatus,
    RegisteredModelStorageMode,
)


def validate_registered_model_compatibility(
    store_root: Union[str, Path],
    model_id: str,
) -> RegisteredModelCompatibilityResult:
    """Validate a registered model against adapter compatibility rules.

    Pure read-only function.  Does not mutate manifests, candidates,
    managed files, or runtime state.

    Args:
        store_root: Path to a bootstrapped model-store.
        model_id: The registered model to validate.

    Returns:
        ``RegisteredModelCompatibilityResult`` with status, adapter mapping,
        evidence, and problems.
    """
    layout = ModelStoreLayout(store_root=Path(store_root).resolve())
    evidence: list[str] = []
    problems: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    def _result(**kw) -> RegisteredModelCompatibilityResult:
        return RegisteredModelCompatibilityResult(
            model_id=model_id,
            evidence=list(evidence),
            problems=list(problems),
            checked_at=now,
            **kw,
        )

    # ── Validate store ────────────────────────────────────────────────
    if not layout.registry_dir.exists():
        problems.append("store_not_bootstrapped")
        return _result()

    if not layout.manifest_path.exists():
        problems.append("manifest_missing")
        return _result()

    # ── Load manifest ─────────────────────────────────────────────────
    try:
        data = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        problems.append("manifest_unreadable")
        return _result()

    manifest = ModelRegistryManifest.from_dict(data)
    if manifest.schema_version != 1:
        problems.append("manifest_schema_unsupported")
        return _result()

    # ── Find registered model ─────────────────────────────────────────
    entry = None
    for m in manifest.models:
        if m.get("model_id") == model_id:
            entry = m
            break

    if entry is None:
        problems.append("model_missing")
        return _result()

    rm = RegisteredModel.from_dict(entry)
    evidence.append("registered_model_found")

    status = rm.status
    if status != RegisteredModelStatus.REGISTERED:
        problems.append("model_status_not_registered")
        return _result(status=RegisteredModelCompatibilityStatus.INCOMPATIBLE,
                       registered=True, detected_format=rm.detected_format)

    # ── Validate storage mode ─────────────────────────────────────────
    if rm.storage_mode != RegisteredModelStorageMode.MANAGED:
        problems.append("unsupported_storage_mode")
        return _result(registered=True, detected_format=rm.detected_format)

    # ── Resolve managed path ──────────────────────────────────────────
    managed_rel = rm.managed_path
    if not managed_rel:
        problems.append("managed_path_missing")
        return _result(registered=True, detected_format=rm.detected_format)

    abs_managed = (layout.store_root / managed_rel).resolve()
    if not str(abs_managed).startswith(str(layout.store_root.resolve())):
        problems.append("managed_path_escapes_store")
        return _result(registered=True, managed_path=managed_rel,
                       detected_format=rm.detected_format)

    if not abs_managed.exists():
        problems.append("managed_path_missing")
        return _result(registered=True, managed_path=managed_rel,
                       absolute_managed_path=str(abs_managed),
                       detected_format=rm.detected_format)

    evidence.append("managed_path_exists")
    evidence.append("managed_path_under_store_root")

    # ── Map format + modalities to adapter ────────────────────────────
    fmt = rm.detected_format
    modalities = list(rm.modalities)
    is_vision = "vision" in modalities

    if fmt == ModelCandidateFormat.MLX:
        evidence.append("format_mlx")
        if is_vision:
            adapter_kind = RegisteredModelAdapterKind.MLX_VLM
            evidence.append("adapter_mlx_vlm")
            evidence.append("modalities_vision")
        else:
            adapter_kind = RegisteredModelAdapterKind.MLX_LM_SERVER
            evidence.append("adapter_mlx_lm_server")
        evidence.append("modalities_text")
    elif fmt == ModelCandidateFormat.GGUF:
        adapter_kind = RegisteredModelAdapterKind.LLAMA_CPP
        evidence.append("format_gguf")
        evidence.append("adapter_llama_cpp")
        if "text" in modalities:
            evidence.append("modalities_text")
    else:
        problems.append("unsupported_format")
        return _result(registered=True, managed_path=managed_rel,
                       absolute_managed_path=str(abs_managed),
                       detected_format=fmt, modalities=modalities,
                       status=RegisteredModelCompatibilityStatus.INCOMPATIBLE)

    # ── Inspect managed files for evidence ────────────────────────────
    advertisable = _check_managed_files(abs_managed, fmt, evidence, problems, rm)

    # ── Determine status ──────────────────────────────────────────────
    if advertisable and not problems:
        compat_status = RegisteredModelCompatibilityStatus.COMPATIBLE
    elif problems:
        compat_status = RegisteredModelCompatibilityStatus.INCOMPATIBLE
    else:
        compat_status = RegisteredModelCompatibilityStatus.INDETERMINATE

    # Preserve evidence from the registered model.
    for ev in rm.evidence:
        if ev not in evidence:
            evidence.append(ev)

    return _result(
        status=compat_status,
        adapter_kind=adapter_kind,
        advertisable=advertisable,
        registered=True,
        managed_path=managed_rel,
        absolute_managed_path=str(abs_managed),
        detected_format=fmt,
        detected_family=rm.detected_family,
        modalities=modalities,
    )


def _check_managed_files(
    abs_path: Path,
    fmt: str,
    evidence: list[str],
    problems: list[str],
    rm: RegisteredModel,
) -> bool:
    """Lightly inspect managed files for evidence.  Returns True if advertisable."""
    if fmt == ModelCandidateFormat.MLX:
        if abs_path.is_dir():
            if (abs_path / "config.json").is_file():
                evidence.append("found_config_json")
            if (abs_path / "tokenizer.json").is_file() or (abs_path / "tokenizer_config.json").is_file():
                evidence.append("found_tokenizer")
            has_safetensors = any(
                f.is_file() and f.suffix == ".safetensors"
                for f in abs_path.iterdir()
            ) or (abs_path / "model.safetensors.index.json").is_file()
            if has_safetensors:
                evidence.append("found_safetensors")

            if "found_config_json" in evidence and ("found_tokenizer" in evidence or "found_safetensors" in evidence):
                return True
            problems.append("insufficient_adapter_evidence")
            return False
        problems.append("managed_path_missing")
        return False

    if fmt == ModelCandidateFormat.GGUF:
        if abs_path.is_file() and abs_path.suffix == ".gguf":
            evidence.append("found_gguf_file")
            return True
        problems.append("insufficient_adapter_evidence")
        return False

    problems.append("unsupported_format")
    return False

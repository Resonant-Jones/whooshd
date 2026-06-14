"""Model registry candidate inspection — classify model artifacts.

Pure inspection functions that examine user-provided paths and produce
structured ``ModelCandidate`` records.  No files are copied, moved,
deleted, or registered.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from whooshd.model_registry.contracts import (
    ModelCandidate,
    ModelCandidateFormat,
    ModelCandidateInspectionResult,
    ModelCandidateStatus,
    ModelStoreLayout,
)


# ── Inspection ─────────────────────────────────────────────────────────────


def inspect_model_candidate(
    source_path: Union[str, Path],
) -> ModelCandidateInspectionResult:
    """Inspect a user-provided model artifact path.

    Classifies the path as a candidate, unsupported, or invalid without
    modifying any files.  Returns structured metadata only.

    Args:
        source_path: Path to a model file or directory.  ``~`` is expanded.

    Returns:
        ``ModelCandidateInspectionResult`` with the candidate record and
        any top-level error.
    """
    raw = str(source_path).strip()
    if not raw:
        return ModelCandidateInspectionResult(
            candidate=_empty_candidate(""),
            error="source_path is empty",
        )

    expanded = Path(os.path.expanduser(raw)).resolve()
    evidence: list[str] = []
    problems: list[str] = []

    # ── Existence check ───────────────────────────────────────────────
    if not expanded.exists():
        problems.append("path_missing")
        return ModelCandidateInspectionResult(
            candidate=ModelCandidate(
                candidate_id=_make_candidate_id(raw),
                status=ModelCandidateStatus.INVALID,
                source_path=str(expanded),
                problems=problems,
                created_at=_now(),
            ),
        )

    # ── GGUF file ─────────────────────────────────────────────────────
    if expanded.is_file() and expanded.suffix == ".gguf":
        evidence.append("found_gguf_file")
        family = _detect_family(expanded)
        return ModelCandidateInspectionResult(
            candidate=ModelCandidate(
                candidate_id=_make_candidate_id(str(expanded), evidence),
                status=ModelCandidateStatus.CANDIDATE,
                source_path=str(expanded),
                detected_format=ModelCandidateFormat.GGUF,
                detected_family=family,
                modalities=["text"],
                evidence=evidence,
                created_at=_now(),
            ),
        )

    # ── Directory inspection ──────────────────────────────────────────
    if expanded.is_dir():
        return _inspect_directory(expanded, evidence, problems)

    # ── Unknown file type ─────────────────────────────────────────────
    problems.append("unsupported_format")
    return ModelCandidateInspectionResult(
        candidate=ModelCandidate(
            candidate_id=_make_candidate_id(str(expanded)),
            status=ModelCandidateStatus.UNSUPPORTED,
            source_path=str(expanded),
            detected_format=ModelCandidateFormat.UNKNOWN,
            problems=problems,
            created_at=_now(),
        ),
    )


def _inspect_directory(
    directory: Path,
    evidence: list[str],
    problems: list[str],
) -> ModelCandidateInspectionResult:
    """Inspect a directory for MLX/HuggingFace-style model files."""
    contents = list(directory.iterdir())
    if not contents:
        problems.append("empty_directory")
        return ModelCandidateInspectionResult(
            candidate=ModelCandidate(
                candidate_id=_make_candidate_id(str(directory)),
                status=ModelCandidateStatus.UNSUPPORTED,
                source_path=str(directory),
                problems=problems,
                created_at=_now(),
            ),
        )

    has_config = (directory / "config.json").is_file()
    has_tokenizer = (directory / "tokenizer.json").is_file() or (
        directory / "tokenizer_config.json"
    ).is_file()
    has_safetensors = any(
        f.is_file() and f.suffix == ".safetensors" for f in contents
    ) or (directory / "model.safetensors.index.json").is_file()

    if has_config:
        evidence.append("found_config_json")
    if has_tokenizer:
        evidence.append("found_tokenizer")
    if has_safetensors:
        evidence.append("found_safetensors")

    # Try to read config.json for family/modality hints.
    family = "unknown"
    modalities: list[str] = ["text"]
    if has_config:
        try:
            config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            problems.append("config_unreadable")
            config = {}

        family = _detect_family_from_config(config)
        if family != "unknown":
            evidence.append(f"model_type_{family}")

        modalities = _detect_modalities(config, evidence)
    else:
        family = _detect_family(directory)

    # Classification.
    has_enough_evidence = has_safetensors and (has_config or has_tokenizer)
    if has_safetensors and not has_config and not has_tokenizer:
        problems.append("ambiguous_candidate")

    if has_enough_evidence:
        status = ModelCandidateStatus.CANDIDATE
        fmt = ModelCandidateFormat.MLX
    else:
        status = ModelCandidateStatus.UNSUPPORTED
        fmt = ModelCandidateFormat.UNKNOWN

    return ModelCandidateInspectionResult(
        candidate=ModelCandidate(
            candidate_id=_make_candidate_id(str(directory), evidence),
            status=status,
            source_path=str(directory),
            detected_format=fmt,
            detected_family=family,
            modalities=modalities,
            evidence=evidence,
            problems=problems,
            created_at=_now(),
        ),
    )


# ── Family detection ───────────────────────────────────────────────────────


def _detect_family(path: Path) -> str:
    """Detect likely model family from path name fragments."""
    name = path.name.lower()
    stem = Path(str(path).rstrip("/")).name.lower()

    if "gemma" in name or "gemma" in stem:
        return "gemma"
    if "qwen" in name or "qwen" in stem:
        return "qwen"
    if "llama" in name or "llama" in stem:
        return "llama"
    return "unknown"


def _detect_family_from_config(config: dict) -> str:
    """Detect model family from config.json fields."""
    model_type = str(config.get("model_type", "")).lower()
    architectures = config.get("architectures", [])

    if "gemma" in model_type:
        return "gemma"
    if "qwen" in model_type:
        return "qwen"
    if "llama" in model_type:
        return "llama"

    for arch in architectures:
        arch_lower = str(arch).lower()
        if "gemma" in arch_lower:
            return "gemma"
        if "qwen" in arch_lower:
            return "qwen"
        if "llama" in arch_lower:
            return "llama"

    return "unknown"


def _detect_modalities(config: dict, evidence: list[str]) -> list[str]:
    """Detect supported modalities from config.json."""
    model_type = str(config.get("model_type", "")).lower()
    text_config = config.get("text_config", {})
    vision_config = config.get("vision_config", {})

    modalities: list[str] = ["text"]

    # Check for vision hints.
    if "vl" in model_type or "vision" in model_type:
        modalities.append("vision")
        return modalities

    if vision_config:
        modalities.append("vision")
        return modalities

    # Check for mm_projector (common in LLaVA-style VLMs).
    if "mm_projector" in str(config).lower():
        modalities.append("vision")
        return modalities

    # Check architectures for multimodal hints.
    for arch in config.get("architectures", []):
        arch_lower = str(arch).lower()
        if any(hint in arch_lower for hint in ("vl", "vision", "multimodal")):
            modalities.append("vision")
            return modalities

    return modalities


# ── Candidate record writing ───────────────────────────────────────────────


def write_candidate_record(
    store_root: Union[str, Path],
    candidate: ModelCandidate,
) -> Path:
    """Write a candidate record to ``registry/candidates/<id>.json``.

    Args:
        store_root: Path to an already-bootstrapped model-store root.
        candidate: The inspected candidate to persist.

    Returns:
        Path to the written candidate record file.

    Raises:
        ValueError: if *store_root* is not a bootstrapped store.
        ValueError: if the candidate record would escape the store root.

    Safety:
      - Writes atomically through ``tmp/``.
      - Never writes to ``registry/models.json``.
      - Never modifies files outside the model-store.
      - Preserves existing candidate records unless content is identical.
    """
    layout = ModelStoreLayout(store_root=Path(store_root).resolve())

    # ── Validate store is bootstrapped ────────────────────────────────
    if not layout.registry_dir.exists():
        raise ValueError(
            f"Model-store not bootstrapped: {layout.registry_dir} does not exist. "
            f"Run bootstrap_model_store() first."
        )

    # ── Validate candidate_id is safe ─────────────────────────────────
    safe_id = _safe_filename(candidate.candidate_id)
    candidates_dir = layout.registry_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    target_path = (candidates_dir / f"{safe_id}.json").resolve()
    if not str(target_path).startswith(str(layout.store_root.resolve())):
        raise ValueError(
            f"Candidate record path {target_path} escapes the store root "
            f"{layout.store_root}"
        )

    # ── Idempotency: skip if unchanged ────────────────────────────────
    new_payload = json.dumps(candidate.to_dict(), indent=2, ensure_ascii=False)
    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
        if existing.strip() == new_payload.strip():
            return target_path

    # ── Atomic write ──────────────────────────────────────────────────
    layout.tmp.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix="candidate-", dir=str(layout.tmp)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(target_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target_path


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_candidate_id(source: str, evidence: list[str] | None = None) -> str:
    """Generate a stable candidate ID from the source path + evidence."""
    raw = f"{source}||{'|'.join(sorted(evidence or []))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _safe_filename(candidate_id: str) -> str:
    """Sanitize a candidate ID for use as a filename."""
    return "".join(c for c in candidate_id if c.isalnum() or c in "_-.")[:64]


def _empty_candidate(source: str) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=_make_candidate_id(source),
        status=ModelCandidateStatus.INVALID,
        source_path=source,
        created_at=_now(),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

"""Model resolver — resolve model paths from predictable filesystem layouts.

Pure, standalone resolution primitive.  Does not touch:
  - Runtime adapters (no loading, warming, execution)
  - API inventory endpoints (/v1/models, /api/tags)
  - Hugging Face download or search
  - Codexify integration
  - Registry ingestion
  - File copying or mutation
"""

from __future__ import annotations

from pathlib import Path

from whooshd.models.types import (
    ModelFormat,
    ModelResolutionRequest,
    ModelResolutionResult,
    ResolutionStatus,
)


# ── Supported explicit formats ─────────────────────────────────────────────

_SUPPORTED_FORMATS: set[str] = {"gguf", "mlx", "safetensors"}

# ── Runtime metadata hints (no execution) ──────────────────────────────────

_RUNTIME_MAP: dict[str, str] = {
    "gguf": "llama_cpp",
    "mlx": "mlx_lm",
    "safetensors": "unsupported",
}


# ── Public API ──────────────────────────────────────────────────────────────


def resolve_model(request: ModelResolutionRequest) -> ModelResolutionResult:
    """Resolve a model ID against ordered filesystem search paths.

    Performs three steps:
      1. Determine format (explicit override or heuristic detection).
      2. For each search path (in order), construct the layout-conformant
         directory and validate it.
      3. Return the first valid hit or a structured failure.

    Args:
        request: A ``ModelResolutionRequest`` with model_id, optional format,
                 optional quant, and ordered search_paths.

    Returns:
        ``ModelResolutionResult`` with status, path, format, runtime metadata,
        and any diagnostic reason.
    """
    model_id = request.model_id

    # ── Step 1: Determine format ───────────────────────────────────────
    fmt = _resolve_format(request.format, model_id)
    if fmt is None:
        return ModelResolutionResult(
            status=ResolutionStatus.UNSUPPORTED_FORMAT.value,
            model_id=model_id,
            format=request.format,
            reason=f"unsupported format: {request.format!r}",
        )

    checked_paths: list[str] = []
    runtime = _RUNTIME_MAP.get(fmt, "unsupported")

    # ── Step 2: Search ─────────────────────────────────────────────────
    publisher, repo = _split_model_id(model_id)
    if publisher is None or repo is None:
        return ModelResolutionResult(
            status=ResolutionStatus.MISSING.value,
            model_id=model_id,
            format=fmt,
            runtime=runtime,
            reason=f"malformed model_id: {model_id!r}",
            metadata={"checked_paths": checked_paths},
        )

    for root in request.search_paths:
        root = root.expanduser().resolve()
        candidate_dir = root / fmt / publisher / repo
        checked_paths.append(str(candidate_dir))

        result = _validate_directory(
            candidate_dir=candidate_dir,
            fmt=fmt,
            quant=request.quant,
            model_id=model_id,
            runtime=runtime,
            checked_paths=checked_paths,
        )
        if result is not None:
            return result

    # ── Step 3: No hit ─────────────────────────────────────────────────
    return ModelResolutionResult(
        status=ResolutionStatus.MISSING.value,
        model_id=model_id,
        format=fmt,
        runtime=runtime,
        reason="model not found in any search path",
        metadata={"checked_paths": checked_paths},
    )


# ── Format resolution ──────────────────────────────────────────────────────


def _resolve_format(explicit_format: str | None, model_id: str) -> str | None:
    """Determine the model format.

    Explicit format overrides detection.  Unknown explicit formats return
    ``None``.
    """
    if explicit_format is not None:
        explicit = explicit_format.strip().lower()
        if explicit in _SUPPORTED_FORMATS:
            return explicit
        return None

    return _detect_format(model_id)


def _detect_format(model_id: str) -> str:
    """Heuristically detect format from the model identifier.

    Rules (in order):
      1. repo id ending in ``-GGUF`` or containing ``GGUF`` → ``gguf``
      2. repo id beginning with ``mlx-community/`` or ending in ``-mlx`` → ``mlx``
      3. otherwise → ``safetensors``
    """
    upper = model_id.upper()
    lower = model_id.lower()

    # Rule 1: GGUF
    if upper.endswith("-GGUF") or "GGUF" in upper:
        return ModelFormat.GGUF.value

    # Rule 2: MLX
    if lower.startswith("mlx-community/") or lower.endswith("-mlx"):
        return ModelFormat.MLX.value

    # Rule 3: default
    return ModelFormat.SAFETENSORS.value


# ── Model ID splitting ─────────────────────────────────────────────────────


def _split_model_id(model_id: str) -> tuple[str | None, str | None]:
    """Split ``Publisher/Repo`` into its two components.

    Returns ``(None, None)`` if the model_id is not in the expected shape.
    """
    parts = model_id.split("/")
    if len(parts) != 2:
        return (None, None)
    publisher, repo = parts
    if not publisher or not repo:
        return (None, None)
    return (publisher, repo)


# ── Directory validation ───────────────────────────────────────────────────


def _validate_directory(
    candidate_dir: Path,
    fmt: str,
    quant: str | None,
    model_id: str,
    runtime: str,
    checked_paths: list[str],
) -> ModelResolutionResult | None:
    """Validate a candidate directory against the format-specific layout
    contract.  Returns a result on hit or structured failure, or ``None``
    to continue searching.
    """
    if not candidate_dir.is_dir():
        return None  # Keep searching other roots.

    metadata: dict = {"checked_paths": list(checked_paths)}

    if fmt == ModelFormat.GGUF.value:
        return _validate_gguf(candidate_dir, quant, model_id, runtime, metadata)

    if fmt == ModelFormat.MLX.value:
        return _validate_mlx(candidate_dir, model_id, runtime, metadata)

    if fmt == ModelFormat.SAFETENSORS.value:
        return _validate_safetensors(candidate_dir, model_id, runtime, metadata)

    # Should not reach here — format is validated earlier.
    return None


# ── GGUF validation ────────────────────────────────────────────────────────


def _validate_gguf(
    directory: Path,
    quant: str | None,
    model_id: str,
    runtime: str,
    metadata: dict,
) -> ModelResolutionResult:
    """Validate a GGUF model directory.

    Valid when:
      - Contains at least one ``.gguf`` file.
      - If *quant* is provided, prefer a file whose name contains the
        quant string case-insensitively.
    """
    gguf_files = sorted(directory.glob("*.gguf"))

    if not gguf_files:
        return ModelResolutionResult(
            status=ResolutionStatus.INVALID_LAYOUT.value,
            model_id=model_id,
            format=ModelFormat.GGUF.value,
            runtime=runtime,
            reason="directory exists but contains no .gguf files",
            metadata=metadata,
        )

    # Quant matching.
    matched: Path | None = None
    if quant is not None and quant.strip():
        quant_lower = quant.strip().lower()
        for gf in gguf_files:
            if quant_lower in gf.name.lower():
                matched = gf
                break

    if matched is None:
        if quant is not None:
            # Quant was requested but no match.
            return ModelResolutionResult(
                status=ResolutionStatus.MISSING.value,
                model_id=model_id,
                format=ModelFormat.GGUF.value,
                runtime=runtime,
                reason=f"no .gguf file matching quant {quant!r}",
                metadata={**metadata, "quant_requested": quant},
            )
        # No quant specified — pick the first (alphabetically).
        matched = gguf_files[0]

    return ModelResolutionResult(
        status=ResolutionStatus.FOUND.value,
        model_id=model_id,
        format=ModelFormat.GGUF.value,
        path=str(matched.resolve()),
        source="local_filesystem",
        runtime=runtime,
        metadata={
            **metadata,
            "matched_file": matched.name,
            "quant": quant,
        },
    )


# ── MLX validation ─────────────────────────────────────────────────────────


def _validate_mlx(
    directory: Path,
    model_id: str,
    runtime: str,
    metadata: dict,
) -> ModelResolutionResult:
    """Validate an MLX model directory.

    Valid when:
      - Directory contains ``config.json``.
    """
    config = directory / "config.json"

    if not config.is_file():
        return ModelResolutionResult(
            status=ResolutionStatus.INVALID_LAYOUT.value,
            model_id=model_id,
            format=ModelFormat.MLX.value,
            runtime=runtime,
            reason="directory exists but lacks config.json",
            metadata=metadata,
        )

    return ModelResolutionResult(
        status=ResolutionStatus.FOUND.value,
        model_id=model_id,
        format=ModelFormat.MLX.value,
        path=str(directory.resolve()),
        source="local_filesystem",
        runtime=runtime,
        metadata={
            **metadata,
            "matched_file": "config.json",
        },
    )


# ── Safetensors validation ─────────────────────────────────────────────────


def _validate_safetensors(
    directory: Path,
    model_id: str,
    runtime: str,
    metadata: dict,
) -> ModelResolutionResult:
    """Validate a safetensors model directory.

    Valid when:
      - Directory contains ``config.json``, or
      - Directory contains at least one ``.safetensors`` file.
    """
    has_config = (directory / "config.json").is_file()
    has_safetensors = bool(list(directory.glob("*.safetensors")))

    if not has_config and not has_safetensors:
        return ModelResolutionResult(
            status=ResolutionStatus.INVALID_LAYOUT.value,
            model_id=model_id,
            format=ModelFormat.SAFETENSORS.value,
            runtime=runtime,
            reason="directory exists but lacks config.json or .safetensors files",
            metadata=metadata,
        )

    matched = "config.json" if has_config else "*.safetensors"
    return ModelResolutionResult(
        status=ResolutionStatus.FOUND.value,
        model_id=model_id,
        format=ModelFormat.SAFETENSORS.value,
        path=str(directory.resolve()),
        source="local_filesystem",
        runtime=runtime,
        metadata={
            **metadata,
            "matched_file": matched,
        },
    )

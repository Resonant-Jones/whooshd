"""External model inventory — scan configured external weight routes.

Produces ``ExternalModelInventoryEntry`` records for models discovered
under available external route roots.  Does not load, warm, or execute
models.  Does not call Hugging Face.  Read-only.
"""

from __future__ import annotations

import re
from pathlib import Path

from whooshd.models.resolver import resolve_model
from whooshd.models.routes import (
    ExternalWeightRoute,
    get_available_route_paths,
    load_external_weight_routes,
    validate_all_routes,
)
from whooshd.models.types import (
    ExternalModelInventoryEntry,
    ExternalRouteStatus,
    ExternalRuntimeResolution,
    ModelFormat,
    ModelResolutionRequest,
    ResolutionStatus,
)


# ── Hidden directory names to skip ─────────────────────────────────────────

_SKIP_DIRS: set[str] = {
    ".cache",
    ".huggingface",
    ".git",
    ".DS_Store",
    "__pycache__",
}

# ── Known GGUF quant patterns ──────────────────────────────────────────────

_KNOWN_QUANTS: set[str] = {
    "Q4_K_M",
    "Q5_K_M",
    "Q8_0",
}

_QUANT_RE = re.compile(
    r"(?i)(Q[0-9]_[KLM0]|Q[0-9]_[0-9]|[fF]16|[fF]32|[bB][fF]16|IQ[0-9]_[^/.]*)"
)


# ── Public API ──────────────────────────────────────────────────────────────


def list_external_model_inventory(
    routes: list[ExternalWeightRoute],
) -> list[ExternalModelInventoryEntry]:
    """Scan all available external routes for models.

    1. Validates all routes (Phase 2).
    2. Keeps only enabled and available routes.
    3. Sorts by Phase 2 priority rules.
    4. Scans each route root using the layout contract.
    5. Returns typed inventory entries.

    Args:
        routes: Configured external weight routes.

    Returns:
        List of ``ExternalModelInventoryEntry`` records.  Empty if no
        routes are configured or available.

    Safety:
      - Read-only.  Never mutates filesystem.
      - Never calls Hugging Face.
      - Never loads models.
    """
    # Validate all routes.
    statuses = validate_all_routes(routes)

    # Available paths, already sorted by Phase 2 priority.
    available_paths = get_available_route_paths(routes)

    # Build a map: resolved path → route id.
    path_to_route: dict[str, str] = {}
    for s in statuses:
        if s.available:
            resolved = str(s.path.resolve())
            path_to_route[resolved] = s.id

    entries: list[ExternalModelInventoryEntry] = []
    seen_ids: set[str] = set()

    for root in available_paths:
        root_resolved = str(root.resolve())
        route_id = path_to_route.get(root_resolved, "unknown")
        _scan_route(root, route_id, entries, seen_ids)

    return entries


# ── Route scanning ─────────────────────────────────────────────────────────


def _scan_route(
    root: Path,
    route_id: str,
    entries: list[ExternalModelInventoryEntry],
    seen_ids: set[str],
) -> None:
    """Scan a single route root for model directories.

    Walks ``<root>/<format>/<publisher>/<repo>`` for gguf, mlx, and
    safetensors.  Skips hidden directories.
    """
    for fmt_dir_name in ("gguf", "mlx", "safetensors"):
        fmt_root = root / fmt_dir_name
        if not fmt_root.is_dir():
            continue

        for publisher_path in sorted(fmt_root.iterdir()):
            if not publisher_path.is_dir():
                continue
            if publisher_path.name.startswith("."):
                continue
            if publisher_path.name in _SKIP_DIRS:
                continue

            publisher = publisher_path.name

            for repo_path in sorted(publisher_path.iterdir()):
                if not repo_path.is_dir():
                    continue
                if repo_path.name.startswith("."):
                    continue
                if repo_path.name in _SKIP_DIRS:
                    continue

                repo = repo_path.name
                model_id = f"{publisher}/{repo}"

                if fmt_dir_name == ModelFormat.GGUF.value:
                    _scan_gguf_dir(model_id, repo_path, route_id, entries, seen_ids)
                else:
                    entry = _build_entry(
                        model_id=model_id,
                        fmt=fmt_dir_name,
                        directory=repo_path,
                        route_id=route_id,
                    )
                    if entry is not None and entry.id not in seen_ids:
                        seen_ids.add(entry.id)
                        entries.append(entry)


# ── Entry building ─────────────────────────────────────────────────────────


def _build_entry(
    model_id: str,
    fmt: str,
    directory: Path,
    route_id: str,
) -> ExternalModelInventoryEntry | None:
    """Build an inventory entry for a single model directory.

    Returns ``None`` if the directory does not meet the minimum layout
    contract for its format.
    """
    if fmt == ModelFormat.GGUF.value:
        return _build_gguf_entry(model_id, directory, route_id)

    if fmt == ModelFormat.MLX.value:
        return _build_mlx_entry(model_id, directory, route_id)

    if fmt == ModelFormat.SAFETENSORS.value:
        return _build_safetensors_entry(model_id, directory, route_id)

    return None


# ── GGUF entry ─────────────────────────────────────────────────────────────


def _build_gguf_entry(
    model_id: str,
    directory: Path,
    route_id: str,
) -> ExternalModelInventoryEntry | None:
    """Build a GGUF inventory entry.

    A valid GGUF directory contains at least one ``.gguf`` file.
    Each ``.gguf`` file becomes a separate inventory entry with its
    own quant-derived public id.
    """
    gguf_files = sorted(directory.glob("*.gguf"))
    if not gguf_files:
        return None

    entries: list[ExternalModelInventoryEntry] = []
    for gf in gguf_files:
        if gf.name.startswith("."):
            continue

        quant = _extract_quant(gf.stem)
        if quant:
            public_id = f"{model_id}:{quant}"
        else:
            # Deterministic fallback using file stem.
            safe_stem = _safe_id_component(gf.stem)
            public_id = f"{model_id}:{safe_stem}"

        entries.append(
            ExternalModelInventoryEntry(
                id=public_id,
                model_id=model_id,
                route_id=route_id,
                format=ModelFormat.GGUF.value,
                runtime="llama_cpp",
                path=str(gf.resolve()),
                registry_managed=False,
                path_available=True,
                servable=True,
                metadata={
                    "matched_file": gf.name,
                    "quant": quant,
                },
            )
        )

    # We need to return one entry per file.  But _scan_route calls
    # _build_entry once per directory.  To support multi-quant GGUF,
    # we return the first and let the caller handle multiple files
    # via a different path.  For Phase 3 simplicity, return all as
    # a list from a separate function.

    # Actually, the cleanest approach: _scan_route should call a
    # separate path for GGUF that handles per-file entries.
    return None  # entry suppressed here — handled by _scan_gguf_dir


def _scan_gguf_dir(
    model_id: str,
    directory: Path,
    route_id: str,
    entries: list[ExternalModelInventoryEntry],
    seen_ids: set[str],
) -> None:
    """Scan a GGUF directory — one entry per .gguf file."""
    gguf_files = sorted(directory.glob("*.gguf"))
    for gf in gguf_files:
        if gf.name.startswith("."):
            continue

        quant = _extract_quant(gf.stem)
        if quant:
            public_id = f"{model_id}:{quant}"
        else:
            safe_stem = _safe_id_component(gf.stem)
            public_id = f"{model_id}:{safe_stem}"

        if public_id in seen_ids:
            continue
        seen_ids.add(public_id)

        entries.append(
            ExternalModelInventoryEntry(
                id=public_id,
                model_id=model_id,
                route_id=route_id,
                format=ModelFormat.GGUF.value,
                runtime="llama_cpp",
                path=str(gf.resolve()),
                registry_managed=False,
                path_available=True,
                servable=True,
                metadata={
                    "matched_file": gf.name,
                    "quant": quant,
                },
            )
        )


# ── MLX entry ──────────────────────────────────────────────────────────────


def _build_mlx_entry(
    model_id: str,
    directory: Path,
    route_id: str,
) -> ExternalModelInventoryEntry | None:
    """Build an MLX inventory entry.

    Valid when directory contains ``config.json``.
    """
    config = directory / "config.json"
    if not config.is_file():
        return None

    return ExternalModelInventoryEntry(
        id=model_id,
        model_id=model_id,
        route_id=route_id,
        format=ModelFormat.MLX.value,
        runtime="mlx_lm",
        path=str(directory.resolve()),
        registry_managed=False,
        path_available=True,
        servable=True,
        metadata={
            "matched_file": "config.json",
        },
    )


# ── Safetensors entry ──────────────────────────────────────────────────────


def _build_safetensors_entry(
    model_id: str,
    directory: Path,
    route_id: str,
) -> ExternalModelInventoryEntry | None:
    """Build a safetensors inventory entry.

    Valid when directory contains ``config.json`` or at least one
    ``.safetensors`` file.  Marked ``servable=False`` because no
    safetensors runtime exists yet.
    """
    has_config = (directory / "config.json").is_file()
    has_safetensors = bool(list(directory.glob("*.safetensors")))

    if not has_config and not has_safetensors:
        return None

    matched = "config.json" if has_config else "*.safetensors"
    return ExternalModelInventoryEntry(
        id=model_id,
        model_id=model_id,
        route_id=route_id,
        format=ModelFormat.SAFETENSORS.value,
        runtime="unsupported",
        path=str(directory.resolve()),
        registry_managed=False,
        path_available=True,
        servable=False,
        metadata={
            "matched_file": matched,
        },
    )


# ── Quant extraction ───────────────────────────────────────────────────────


def _extract_quant(stem: str) -> str | None:
    """Extract a quantization label from a GGUF file stem.

    Checks known quants first (Q4_K_M, Q5_K_M, Q8_0), then falls back
    to regex-based extraction.

    Returns ``None`` if no quant pattern is found.
    """
    # Check known quants (case-insensitive).
    upper = stem.upper()
    for kq in _KNOWN_QUANTS:
        if kq in upper:
            return kq

    # Regex fallback.
    m = _QUANT_RE.search(stem)
    if m:
        return m.group(1).upper()

    return None


# ── Helpers ────────────────────────────────────────────────────────────────


def _safe_id_component(name: str) -> str:
    """Sanitize a name component for use in a public model id."""
    # Replace unsafe chars, collapse, trim.
    safe = re.sub(r"[^a-zA-Z0-9._-]", "-", name)[:64].strip("-.")
    if not safe:
        safe = "unknown"
    return safe


def _is_hidden(name: str) -> bool:
    """Check if a directory name should be skipped."""
    return name.startswith(".") or name in _SKIP_DIRS


# ── External model public ID parsing ──────────────────────────────────────


def parse_external_model_public_id(model: str) -> dict[str, str | None]:
    """Parse a public external model ID into its components.

    GGUF: ``Publisher/Repo:QUANT`` → model_id, gguf, quant
    MLX:  ``Publisher/Repo``       → model_id, mlx, None
    Safetensors: ``Publisher/Repo`` → model_id, safetensors, None

    Returns a dict with keys: ``model_id``, ``format``, ``quant``.
    """
    model = model.strip()

    # GGUF: check for colon-separated quant suffix.
    if ":" in model and "/" in model:
        parts = model.rsplit(":", 1)
        candidate_id = parts[0]
        suffix = parts[1]
        # Heuristic: if the part before colon has a slash, it looks like Publisher/Repo.
        # The suffix is the quant or file stem.
        upper = candidate_id.upper()
        if "GGUF" in upper:
            return {"model_id": candidate_id, "format": "gguf", "quant": suffix}
        # Could be an MLX model with colon in name — check heuristics.
        if upper.endswith("-GGUF"):
            return {"model_id": candidate_id, "format": "gguf", "quant": suffix}

    # MLX heuristic.
    lower = model.lower()
    if lower.startswith("mlx-community/") or lower.endswith("-mlx"):
        return {"model_id": model, "format": "mlx", "quant": None}

    # Safetensors default.
    if "/" in model:
        return {"model_id": model, "format": "safetensors", "quant": None}

    # Fallback: treat as safetensors.
    return {"model_id": model, "format": "safetensors", "quant": None}


# ── External runtime resolution ───────────────────────────────────────────


def resolve_external_runtime_model(
    requested_model: str,
    routes: list[ExternalWeightRoute],
) -> ExternalRuntimeResolution:
    """Resolve an external model ID for runtime handoff.

    1. Parses the public ID.
    2. Scans external inventory for a matching entry.
    3. If found and servable, resolves the path via Phase 1 resolver.
    4. Returns a structured resolution result.

    Args:
        requested_model: Public model ID from the API request.
        routes: Configured external weight routes.

    Returns:
        ``ExternalRuntimeResolution`` with found/servable/path/runtime.
    """
    parsed = parse_external_model_public_id(requested_model)
    fmt = parsed["format"]
    _, quant = parsed.get("model_id"), parsed.get("quant")

    # Scan inventory for a matching entry.
    inventory = list_external_model_inventory(routes)
    match: ExternalModelInventoryEntry | None = None
    for entry in inventory:
        if entry.id == requested_model:
            match = entry
            break

    if match is None:
        return ExternalRuntimeResolution(
            found=False,
            public_id=requested_model,
            format=fmt,
            reason="not_external",
        )

    if not match.servable:
        return ExternalRuntimeResolution(
            found=True,
            servable=False,
            model_id=match.model_id,
            public_id=requested_model,
            format=match.format,
            runtime=match.runtime,
            route_id=match.route_id,
            reason="not_servable",
        )

    # Resolve the path through Phase 1 resolver with route paths.
    from whooshd.models.routes import get_available_route_paths

    available_paths = get_available_route_paths(routes)
    if not available_paths:
        return ExternalRuntimeResolution(
            found=True,
            servable=False,
            model_id=match.model_id,
            public_id=requested_model,
            format=match.format,
            runtime=match.runtime,
            route_id=match.route_id,
            reason="route_unavailable",
        )

    req = ModelResolutionRequest(
        model_id=match.model_id if match.model_id else requested_model,
        format=match.format,
        quant=quant if quant else None,
        search_paths=available_paths,
    )
    result = resolve_model(req)

    if result.status != ResolutionStatus.FOUND.value:
        return ExternalRuntimeResolution(
            found=True,
            servable=False,
            model_id=match.model_id,
            public_id=requested_model,
            format=match.format,
            runtime=match.runtime,
            route_id=match.route_id,
            reason="invalid_layout",
            metadata={"resolution_status": result.status, "reason": result.reason},
        )

    return ExternalRuntimeResolution(
        found=True,
        servable=True,
        model_id=match.model_id,
        public_id=requested_model,
        format=match.format,
        runtime=match.runtime,
        path=result.path,
        route_id=match.route_id,
        metadata={
            "quant": quant,
            "matched_file": result.metadata.get("matched_file"),
        },
    )

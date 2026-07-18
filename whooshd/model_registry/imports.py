"""Model registry import workflow for local MLX cache snapshots.

This module provides a narrow, first-class import path that:
  - bootstraps the managed model-store
  - scans likely local MLX cache roots
  - inspects discovered snapshots using the existing candidate inspector
  - writes candidate records
  - registers compatible candidates into the managed store
  - reports which registered models are advertisable in ``/v1/models``

It deliberately reuses the existing model-registry seams rather than
introducing a parallel registry or model catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, Union

from whooshd.config import get_model_store_root
from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import (
    inspect_model_candidate,
    write_candidate_record,
)
from whooshd.model_registry.contracts import (
    ModelCandidateFormat,
    ModelCandidateStatus,
    ModelRegistryState,
    RegisteredModel,
)
from whooshd.model_registry.inventory import (
    collect_advertisable_registered_models,
)
from whooshd.model_registry.registration import (
    _validate_model_id,
    register_model_candidate,
)


# ── Default local cache roots ──────────────────────────────────────────────


def default_local_mlx_scan_roots() -> list[Path]:
    """Return the preferred local cache roots to scan for MLX snapshots.

    The importer prefers the HF hub cache on this machine, but falls back
    to the parent Hugging Face cache directory if the hub directory is
    absent.  This keeps the default surface narrow while still supporting
    the common macOS and Unix layouts.
    """
    home = Path.home()
    candidates = (
        (home / ".cache" / "huggingface" / "hub", home / ".cache" / "huggingface"),
        (
            home / "Library" / "Caches" / "huggingface" / "hub",
            home / "Library" / "Caches" / "huggingface",
        ),
    )

    roots: list[Path] = []
    seen: set[str] = set()
    for hub_root, parent_root in candidates:
        chosen = hub_root if hub_root.exists() else parent_root
        resolved = chosen.expanduser().resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)

    if roots:
        return roots

    # Fall back to the canonical hub paths if neither cache exists yet.
    return [
        (home / ".cache" / "huggingface" / "hub").resolve(),
        (home / "Library" / "Caches" / "huggingface" / "hub").resolve(),
    ]


# ── Discovery contracts ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LocalMlxSnapshotSource:
    """A discovered MLX snapshot source inside a local cache root."""

    cache_root: str
    repo_id: str
    repo_dir: str
    snapshot_path: str
    selected_by: str = "refs/main"


@dataclass
class LocalMlxImportRecord:
    """Outcome for one discovered local MLX snapshot."""

    repo_id: str
    model_id: str
    source_path: str
    candidate_id: str = ""
    candidate_status: str = ""
    candidate_format: str = ""
    candidate_family: str = ""
    modalities: list[str] = field(default_factory=list)
    status: str = ""
    reason: str | None = None
    managed_path: str = ""
    manifest_updated: bool = False


@dataclass
class LocalMlxImportReport:
    """Aggregate report for a managed local MLX import run."""

    store_root: str
    bootstrap_state: ModelRegistryState
    scan_roots: list[str] = field(default_factory=list)
    sources: list[LocalMlxSnapshotSource] = field(default_factory=list)
    records: list[LocalMlxImportRecord] = field(default_factory=list)
    advertisable_models: list[RegisteredModel] = field(default_factory=list)
    error: str | None = None

    @property
    def imported_records(self) -> list[LocalMlxImportRecord]:
        return [record for record in self.records if record.status == "imported"]

    @property
    def duplicate_records(self) -> list[LocalMlxImportRecord]:
        return [record for record in self.records if record.status == "duplicate"]

    @property
    def skipped_records(self) -> list[LocalMlxImportRecord]:
        return [record for record in self.records if record.status == "skipped"]

    @property
    def already_registered_records(self) -> list[LocalMlxImportRecord]:
        return [record for record in self.records if record.status == "already_registered"]

    @property
    def advertisable_model_ids(self) -> list[str]:
        return [model.model_id for model in self.advertisable_models]


# ── Public API ─────────────────────────────────────────────────────────────


def discover_local_mlx_snapshot_sources(
    scan_roots: Sequence[Union[str, Path]] | None = None,
) -> list[LocalMlxSnapshotSource]:
    """Discover likely MLX snapshot sources under cache roots.

    The scanner intentionally stays narrow:
      - It only looks for Hugging Face-style ``models--publisher--repo``
        cache directories.
      - It selects the active snapshot referenced by ``refs/main`` when
        present.
      - If no main ref exists, it falls back to a single snapshot tree.
      - It does not recurse through arbitrary filesystem trees.

    Duplicate source paths are deduplicated.  Duplicate model IDs are left
    for the registration seam to handle.
    """
    roots = _normalize_scan_roots(scan_roots)
    sources: list[LocalMlxSnapshotSource] = []
    seen_snapshot_paths: set[str] = set()

    for scan_root in roots:
        for cache_root in _candidate_cache_roots(scan_root):
            if not cache_root.is_dir():
                continue

            for repo_dir in sorted(cache_root.glob("models--*--*")):
                if not repo_dir.is_dir():
                    continue

                repo_id = _parse_hf_repo_id(repo_dir.name)
                if repo_id is None:
                    continue

                snapshot_path, selected_by = _select_snapshot_path(repo_dir)
                if snapshot_path is None:
                    continue

                resolved_snapshot = str(snapshot_path.resolve())
                if resolved_snapshot in seen_snapshot_paths:
                    continue
                seen_snapshot_paths.add(resolved_snapshot)

                sources.append(
                    LocalMlxSnapshotSource(
                        cache_root=str(cache_root.resolve()),
                        repo_id=repo_id,
                        repo_dir=str(repo_dir.resolve()),
                        snapshot_path=resolved_snapshot,
                        selected_by=selected_by,
                    )
                )

    return sources


def import_local_mlx_models(
    store_root: Union[str, Path, None] = None,
    scan_roots: Sequence[Union[str, Path]] | None = None,
) -> LocalMlxImportReport:
    """Import compatible local MLX snapshots into the managed store."""
    resolved_store_root = _resolve_store_root(store_root)
    bootstrap_state = bootstrap_model_store(resolved_store_root)
    report = LocalMlxImportReport(
        store_root=bootstrap_state.store_root,
        bootstrap_state=bootstrap_state,
        scan_roots=[str(root) for root in _normalize_scan_roots(scan_roots)],
    )

    if bootstrap_state.error:
        report.error = bootstrap_state.error
        return report

    sources = discover_local_mlx_snapshot_sources(scan_roots)
    report.sources = sources

    for source in sources:
        inspection = inspect_model_candidate(source.snapshot_path)
        candidate = inspection.candidate
        model_id = _model_id_from_repo_id(source.repo_id)
        record = LocalMlxImportRecord(
            repo_id=source.repo_id,
            model_id=model_id or "",
            source_path=source.snapshot_path,
            candidate_id=candidate.candidate_id,
            candidate_status=candidate.status,
            candidate_format=candidate.detected_format,
            candidate_family=candidate.detected_family,
            modalities=list(candidate.modalities),
        )

        # Persist the inspection artifact even if the source does not
        # register cleanly.  This keeps the managed store audit trail intact.
        try:
            write_candidate_record(resolved_store_root, candidate)
        except Exception as exc:  # pragma: no cover - defensive guard
            record.status = "error"
            record.reason = f"candidate_record_write_failed: {exc}"
            report.records.append(record)
            continue

        if inspection.error is not None:
            record.status = "skipped"
            record.reason = inspection.error
            report.records.append(record)
            continue

        if candidate.status != ModelCandidateStatus.CANDIDATE:
            record.status = "skipped"
            record.reason = candidate.problems[0] if candidate.problems else candidate.status
            report.records.append(record)
            continue

        if candidate.detected_format != ModelCandidateFormat.MLX:
            record.status = "skipped"
            record.reason = "unsupported_format"
            report.records.append(record)
            continue

        if not model_id or _validate_model_id(model_id) is not None:
            record.status = "skipped"
            record.reason = "unsafe_model_id"
            report.records.append(record)
            continue

        registration = register_model_candidate(
            resolved_store_root,
            candidate.candidate_id,
            model_id=model_id,
            display_name=source.repo_id,
        )

        record.managed_path = registration.managed_path
        record.manifest_updated = registration.manifest_updated

        if registration.problem is None:
            record.status = "imported" if registration.manifest_updated else "already_registered"
            report.records.append(record)
            continue

        if registration.problem == "duplicate_model_id":
            record.status = "duplicate"
            record.reason = registration.problem
            report.records.append(record)
            continue

        record.status = "error"
        record.reason = registration.problem
        report.records.append(record)

    report.advertisable_models = collect_advertisable_registered_models(resolved_store_root)
    return report


def format_local_mlx_import_report(report: LocalMlxImportReport) -> str:
    """Render a compact human-readable import report."""
    lines: list[str] = []
    lines.append(f"Model-store: {report.store_root}")
    if report.error:
        lines.append(f"Bootstrap error: {report.error}")
        return "\n".join(lines)

    lines.append("Scan roots:")
    for root in report.scan_roots:
        lines.append(f"  - {root}")

    lines.append(f"Discovered snapshots: {len(report.sources)}")
    for source in report.sources:
        lines.append(
            f"  - {source.repo_id} -> {source.snapshot_path} "
            f"({source.selected_by})"
        )

    lines.append("Import results:")
    for record in report.records:
        detail = record.reason or record.managed_path or ""
        if record.status == "imported":
            detail = record.managed_path
        elif record.status == "already_registered":
            detail = record.managed_path or "already registered"
        lines.append(
            f"  - {record.repo_id} [{record.status}] {record.model_id}"
            + (f" -> {detail}" if detail else "")
        )

    lines.append("Advertisable in /v1/models:")
    if report.advertisable_models:
        for model in report.advertisable_models:
            lines.append(f"  - {model.model_id}")
    else:
        lines.append("  - none")

    return "\n".join(lines)


# ── Internal helpers ───────────────────────────────────────────────────────


def _resolve_store_root(store_root: Union[str, Path, None]) -> Path:
    if store_root is None:
        configured = get_model_store_root()
        if configured:
            return Path(configured).expanduser().resolve()
        return (Path.home() / "whooshd-models").resolve()
    return Path(store_root).expanduser().resolve()


def _normalize_scan_roots(
    scan_roots: Sequence[Union[str, Path]] | None,
) -> list[Path]:
    if scan_roots is None:
        return default_local_mlx_scan_roots()

    roots: list[Path] = []
    seen: set[str] = set()
    for raw in scan_roots:
        resolved = Path(raw).expanduser().resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _candidate_cache_roots(scan_root: Path) -> list[Path]:
    """Return the scan root plus an optional ``hub`` child."""
    roots = [scan_root]
    if scan_root.name != "hub":
        roots.append(scan_root / "hub")
    return roots


def _parse_hf_repo_id(cache_dir_name: str) -> str | None:
    """Parse ``models--publisher--repo`` into ``publisher/repo``."""
    parts = cache_dir_name.split("--", 2)
    if len(parts) != 3 or parts[0] != "models":
        return None
    publisher, repo = parts[1], parts[2]
    if not publisher or not repo:
        return None
    return f"{publisher}/{repo}"


def _select_snapshot_path(repo_dir: Path) -> tuple[Path | None, str]:
    """Select the snapshot tree to import for a Hugging Face cache repo."""
    snapshots_dir = repo_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return (None, "snapshots_missing")

    main_ref = repo_dir / "refs" / "main"
    if main_ref.is_file():
        try:
            snapshot_hash = main_ref.read_text(encoding="utf-8").strip()
        except OSError:
            snapshot_hash = ""
        if snapshot_hash:
            main_snapshot = snapshots_dir / snapshot_hash
            if main_snapshot.is_dir():
                return (main_snapshot, "refs/main")

    snapshots = sorted(path for path in snapshots_dir.iterdir() if path.is_dir())
    if len(snapshots) == 1:
        return (snapshots[0], "single_snapshot_fallback")

    return (None, "ambiguous_snapshots")


def _model_id_from_repo_id(repo_id: str) -> str | None:
    """Normalize ``publisher/repo`` into the slash-free registry model ID."""
    if "/" not in repo_id:
        return None
    publisher, repo = repo_id.split("/", 1)
    raw = f"{publisher}-{repo}"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", raw).strip("-.")
    if not safe:
        return None
    return safe[:128]

"""External weight routes — configured external model storage roots.

Provides route loading from environment configuration, route availability
validation, mount detection, priority ordering, and a route-aware resolver
that composes with the Phase 1 ``ModelResolver``.

Read-only subsystem.  No filesystem mutation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from whooshd.models.resolver import resolve_model
from whooshd.models.types import (
    ExternalRouteStatus,
    ExternalWeightRoute,
    ExternalWeightRouteStatus,
    ModelResolutionRequest,
    ModelResolutionResult,
    ResolutionStatus,
)


# ── Environment variable ───────────────────────────────────────────────────

_ENV_EXTERNAL_ROUTES = "WHOOSHD_EXTERNAL_ROUTES"


# ── Route loading ──────────────────────────────────────────────────────────


def load_external_weight_routes() -> list[ExternalWeightRoute]:
    """Load external weight routes from the environment.

    Reads ``WHOOSHD_EXTERNAL_ROUTES``, which must be a JSON array of route
    objects.  Each object must have an ``id`` (str) and ``path`` (str).
    Optional fields: ``enabled`` (bool, default ``true``), ``read_only``
    (bool, default ``true``), ``priority`` (int, default ``100``).

    Returns:
        List of ``ExternalWeightRoute`` entries.  Returns an empty list
        if the env var is unset, empty, or unparseable (no crash).
    """
    raw = os.environ.get(_ENV_EXTERNAL_ROUTES, "").strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    routes: list[ExternalWeightRoute] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("id")
        rpath = entry.get("path")
        if not rid or not rpath:
            continue
        if not isinstance(rid, str) or not isinstance(rpath, str):
            continue
        routes.append(
            ExternalWeightRoute(
                id=rid.strip(),
                path=Path(rpath.strip()).expanduser(),
                enabled=bool(entry.get("enabled", True)),
                read_only=bool(entry.get("read_only", True)),
                priority=int(entry.get("priority", 100)),
            )
        )
    return routes


# ── Route validation ───────────────────────────────────────────────────────


def validate_route_status(route: ExternalWeightRoute) -> ExternalWeightRouteStatus:
    """Validate the filesystem availability of a single external weight route.

    Rules:
      - Disabled → ``disabled``.
      - Path does not exist and appears under a mount point that is missing
        (e.g. ``/Volumes/<name>/...`` where ``/Volumes/<name>`` does not
        exist) → ``mount_unavailable``.
      - Path does not exist and is not under a mount point → ``invalid_path``.
      - Path exists but is not a directory → ``invalid_path``.
      - Path exists and is a directory → ``available``.

    Args:
        route: An ``ExternalWeightRoute`` to validate.

    Returns:
        ``ExternalWeightRouteStatus`` with availability details.
    """
    if not route.enabled:
        return ExternalWeightRouteStatus(
            id=route.id,
            path=route.path,
            enabled=False,
            available=False,
            status=ExternalRouteStatus.DISABLED.value,
            reason="route is disabled",
        )

    rpath = route.path.expanduser().resolve()

    if rpath.exists():
        if rpath.is_dir():
            return ExternalWeightRouteStatus(
                id=route.id,
                path=rpath,
                enabled=True,
                available=True,
                status=ExternalRouteStatus.AVAILABLE.value,
            )
        else:
            return ExternalWeightRouteStatus(
                id=route.id,
                path=rpath,
                enabled=True,
                available=False,
                status=ExternalRouteStatus.INVALID_PATH.value,
                reason="path exists but is not a directory",
            )

    # Path does not exist — check for mount-point pattern.
    if _is_mount_unavailable(rpath):
        return ExternalWeightRouteStatus(
            id=route.id,
            path=rpath,
            enabled=True,
            available=False,
            status=ExternalRouteStatus.MOUNT_UNAVAILABLE.value,
            reason=f"mount point unavailable: {_mount_point(rpath)}",
        )

    return ExternalWeightRouteStatus(
        id=route.id,
        path=rpath,
        enabled=True,
        available=False,
        status=ExternalRouteStatus.INVALID_PATH.value,
        reason="path does not exist",
    )


def validate_all_routes(
    routes: list[ExternalWeightRoute],
) -> list[ExternalWeightRouteStatus]:
    """Validate every route in the list.  Returns one status per route."""
    return [validate_route_status(r) for r in routes]


# ── Mount detection ────────────────────────────────────────────────────────


def _is_mount_unavailable(path: Path) -> bool:
    """Detect whether *path* is under a missing macOS-style mount point.

    Heuristic: if any ancestor of *path* matches ``/Volumes/<name>`` and
    that directory does not exist, the mount is unavailable.
    """
    parts = path.parts
    # Look for /Volumes/<name> pattern.
    for i, part in enumerate(parts):
        if part == "Volumes" and i == 1 and parts[0] == "/":
            # Next part is the volume name.
            if i + 1 < len(parts):
                mount = Path(*parts[: i + 2])
                if not mount.exists():
                    return True
    return False


def _mount_point(path: Path) -> str:
    """Extract the mount point name from a path for diagnostic messages."""
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "Volumes" and i == 1 and parts[0] == "/":
            if i + 1 < len(parts):
                return str(Path(*parts[: i + 2]))
    return str(path)


# ── Available path ordering ────────────────────────────────────────────────


def get_available_route_paths(
    routes: list[ExternalWeightRoute],
) -> list[Path]:
    """Return ordered, validated route paths suitable for resolver search.

    1. Filter: enabled only.
    2. Validate availability.  Unavailable routes are excluded.
    3. Sort by ascending priority.  Equal priorities sort by route id.
    4. Return resolved paths in order.

    Returns:
        Ordered list of ``Path`` entries.
    """
    # Validate all enabled routes.
    statuses = [
        validate_route_status(r) for r in routes if r.enabled
    ]

    # Keep only available.
    available = [s for s in statuses if s.available]

    # Sort: ascending priority, then by id for determinism.
    rid_to_route = {r.id: r for r in routes}
    available.sort(key=lambda s: (rid_to_route.get(s.id, ExternalWeightRoute(id="", path=Path("."))).priority, s.id))

    return [s.path for s in available]


# ── Route-aware resolver ────────────────────────────────────────────────────


def resolve_model_from_routes(
    request: ModelResolutionRequest,
    routes: list[ExternalWeightRoute],
) -> ModelResolutionResult:
    """Resolve a model using configured external weight routes.

    Composes with Phase 1 ``resolve_model``:

    1. Validates all routes, collecting statuses.
    2. Filters to available routes, ordered by priority.
    3. Passes ordered route paths as search paths to ``resolve_model``.
    4. Enriches the result metadata with route information.
    5. Sets ``source = "external"`` on successful resolution.

    Args:
        request: A ``ModelResolutionRequest``.  Any ``search_paths`` in
                 the request are ignored — routes supply the search paths.
        routes: Configured external weight routes.

    Returns:
        ``ModelResolutionResult`` with enriched route metadata.
    """
    # Validate all routes.
    all_statuses = validate_all_routes(routes)

    # Build route metadata.
    route_ids_checked: list[str] = []
    available_route_ids: list[str] = []
    unavailable_routes: list[dict] = []

    for s in all_statuses:
        route_ids_checked.append(s.id)
        if s.available:
            available_route_ids.append(s.id)
        else:
            unavailable_routes.append({
                "id": s.id,
                "status": s.status,
                "path": str(s.path),
                "reason": s.reason,
            })

    # Collect sorted available paths.
    available_paths = get_available_route_paths(routes)

    if not available_paths:
        # No route is available — return missing with route context.
        return ModelResolutionResult(
            status=ResolutionStatus.MISSING.value,
            model_id=request.model_id,
            format=request.format,
            reason="no external weight routes are available",
            metadata={
                "route_ids_checked": route_ids_checked,
                "available_route_ids": available_route_ids,
                "unavailable_routes": unavailable_routes,
            },
        )

    # Delegate to Phase 1 resolver with ordered route paths.
    req = ModelResolutionRequest(
        model_id=request.model_id,
        format=request.format,
        quant=request.quant,
        search_paths=available_paths,
    )
    result = resolve_model(req)

    # Enrich metadata with route information.
    enriched_meta: dict = {
        **result.metadata,
        "route_ids_checked": route_ids_checked,
        "available_route_ids": available_route_ids,
        "unavailable_routes": unavailable_routes,
    }

    if result.status == ResolutionStatus.FOUND.value:
        # Override source to "external" for hits from configured routes.
        return ModelResolutionResult(
            status=result.status,
            model_id=result.model_id,
            format=result.format,
            path=result.path,
            source="external",
            runtime=result.runtime,
            reason=result.reason,
            metadata=enriched_meta,
        )

    # Non-found results — preserve status but add route context.
    return ModelResolutionResult(
        status=result.status,
        model_id=result.model_id,
        format=result.format,
        path=result.path,
        source=result.source,
        runtime=result.runtime,
        reason=result.reason or "model not found in any external weight route",
        metadata=enriched_meta,
    )

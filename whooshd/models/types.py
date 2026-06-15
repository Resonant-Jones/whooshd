"""Model resolution types — request/result structures for the resolver core.

These are standalone primitives.  They do not depend on runtime adapters,
API handlers, Hugging Face libraries, or Codexify integration code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ── Status ──────────────────────────────────────────────────────────────────


class ResolutionStatus(str, Enum):
    """Well-known resolution statuses.

    ``found``: model path was located and validated.
    ``missing``: no valid model found in any search path.
    ``invalid_layout``: directory exists but breaks the layout contract.
    ``unsupported_format``: explicit format is not supported.
    """

    FOUND = "found"
    MISSING = "missing"
    INVALID_LAYOUT = "invalid_layout"
    UNSUPPORTED_FORMAT = "unsupported_format"


# ── Format ──────────────────────────────────────────────────────────────────


class ModelFormat(str, Enum):
    """Supported model formats for path resolution."""

    GGUF = "gguf"
    MLX = "mlx"
    SAFETENSORS = "safetensors"


# ── Request ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelResolutionRequest:
    """A request to resolve a model on the local filesystem.

    Fields:
        model_id: Publisher/Repo identifier (e.g. ``Qwen/Qwen3-14B-GGUF``).
        format: Optional explicit format override.  If ``None``, format is
                detected heuristically from *model_id*.
        quant: Optional quantization string (e.g. ``Q4_K_M``).  Only
               meaningful for GGUF models.
        search_paths: Ordered list of root directories to search.  Each
                      path is tested in order using the layout contract
                      ``<root>/<format>/<Publisher>/<Repo>/``.
    """

    model_id: str
    format: str | None = None
    quant: str | None = None
    search_paths: list[Path] = field(default_factory=list)


# ── Result ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelResolutionResult:
    """The result of resolving a model on the local filesystem.

    Fields:
        status: One of ``found``, ``missing``, ``invalid_layout``,
                ``unsupported_format``.
        model_id: Echoed from the request.
        format: Detected or explicit format.
        path: Resolved absolute path, or ``None``.
        source: ``local_filesystem`` when found through direct search paths,
                ``external`` when found through an external weight route.
                Reserved for future sources (e.g. ``huggingface``).
        runtime: Runtime metadata hint (``llama_cpp``, ``mlx_lm``,
                 ``unsupported``).
        reason: Human-readable reason when not ``found``.
        metadata: Additional structured metadata (checked_paths, matched_file,
                  quant, route_ids_checked, etc.).
    """

    status: str = ResolutionStatus.MISSING.value
    model_id: str = ""
    format: str | None = None
    path: str | None = None
    source: str | None = None
    runtime: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── External weight routes ─────────────────────────────────────────────────


class ExternalRouteStatus(str, Enum):
    """Well-known external route availability statuses.

    ``available``: route is enabled and path exists as a directory.
    ``disabled``: route is configured but disabled.
    ``mount_unavailable``: route path is under a missing mounted volume.
    ``invalid_path``: route path exists but is not a directory, or the
                     path shape is unusable.
    """

    AVAILABLE = "available"
    DISABLED = "disabled"
    MOUNT_UNAVAILABLE = "mount_unavailable"
    INVALID_PATH = "invalid_path"


@dataclass(frozen=True)
class ExternalWeightRoute:
    """A configured external weight route.

    Fields:
        id: Unique route identifier (e.g. ``vaultnode``).
        path: Filesystem root for this route's model layout.
        enabled: Whether the route is active.
        read_only: Whether the route forbids writes.
        priority: Lower numbers win when ordering routes.
    """

    id: str
    path: Path
    enabled: bool = True
    read_only: bool = True
    priority: int = 100


@dataclass(frozen=True)
class ExternalWeightRouteStatus:
    """The validated status of an external weight route.

    Fields:
        id: Route identifier (echoed from config).
        path: Route path (echoed from config).
        enabled: Whether the route is enabled.
        available: True when the route is usable for resolution.
        status: One of ``available``, ``disabled``, ``mount_unavailable``,
                ``invalid_path``.
        reason: Human-readable reason when not available.
    """

    id: str
    path: Path
    enabled: bool
    available: bool = False
    status: str = ExternalRouteStatus.DISABLED.value
    reason: str | None = None

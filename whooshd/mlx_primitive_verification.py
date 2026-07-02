"""MLX primitive verification — key-checking, not key-cutting.

Verifies which continuous batching primitives MLX actually supports.
All six remain blocking until backend-verified.  This is MLX emptying
its pockets on the table — one key at a time.  🗝️
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MLXPrimitiveVerificationStatus(str, Enum):
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    FAKE_BOUNDARY_VERIFIED = "fake_boundary_verified"
    SURFACE_AVAILABLE = "surface_available"
    PARTIAL = "partial"
    VERIFIED = "verified"


class MLXPrimitiveName(str, Enum):
    SLOT_OWNERSHIP = "slot_ownership"
    CANCELLATION_HOOK = "cancellation_hook"
    TIMEOUT_HOOK = "timeout_hook"
    SAMPLING_STATE = "sampling_state"
    FAILURE_ISOLATION = "failure_isolation"
    CLEANUP_HOOK = "cleanup_hook"


@dataclass(frozen=True)
class MLXPrimitiveVerification:
    primitive: MLXPrimitiveName
    status: MLXPrimitiveVerificationStatus = MLXPrimitiveVerificationStatus.UNKNOWN
    backend_verified: bool = False
    fake_boundary_verified: bool = False
    blocks_live_continuous_batching: bool = True
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MLXPrimitiveVerificationReport:
    backend: str = "mlx"
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    all_backend_verified: bool = False
    blocking_primitives: tuple[MLXPrimitiveName, ...] = ()
    verifications: tuple[MLXPrimitiveVerification, ...] = ()
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


# ── Per-primitive probes ───────────────────────────────────────────────────


def verify_mlx_slot_ownership_surface() -> MLXPrimitiveVerification:
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.SLOT_OWNERSHIP,
        status=MLXPrimitiveVerificationStatus.UNSUPPORTED,
        notes=("MLX generation surfaces do not expose a slot ownership protocol",),
    )


def verify_mlx_cancellation_surface() -> MLXPrimitiveVerification:
    notes = []
    status = MLXPrimitiveVerificationStatus.PARTIAL
    try:
        from mlx_lm import stream_generate  # noqa: F401
        notes.append("stream_generate can be stopped at the caller boundary")
    except ImportError:
        status = MLXPrimitiveVerificationStatus.UNKNOWN
        notes.append("mlx_lm.stream_generate not importable")
    notes.append("backend cancellation during active decode step is not proven")
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.CANCELLATION_HOOK,
        status=status,
        notes=tuple(notes),
    )


def verify_mlx_timeout_surface() -> MLXPrimitiveVerification:
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.TIMEOUT_HOOK,
        status=MLXPrimitiveVerificationStatus.PARTIAL,
        notes=(
            "Whoosh'd can enforce timeout around generator iteration",
            "backend timeout with slot cleanup is not proven",
        ),
    )


def verify_mlx_sampling_state_surface() -> MLXPrimitiveVerification:
    status = MLXPrimitiveVerificationStatus.SURFACE_AVAILABLE
    notes: list[str] = []
    try:
        from mlx_lm import generate  # noqa: F401
        notes.append("generate() accepts sampling parameters (temperature, top_p, max_tokens)")
    except ImportError:
        status = MLXPrimitiveVerificationStatus.UNKNOWN
        notes.append("mlx_lm.generate not importable")
    notes.append("per-request isolation in continuous decode loop not proven")
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.SAMPLING_STATE,
        status=status,
        notes=tuple(notes),
    )


def verify_mlx_failure_isolation_surface() -> MLXPrimitiveVerification:
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.FAILURE_ISOLATION,
        status=MLXPrimitiveVerificationStatus.UNKNOWN,
        notes=(
            "one generator failure can be caught",
            "per-request failure isolation in shared decode group not proven",
        ),
    )


def verify_mlx_cleanup_surface() -> MLXPrimitiveVerification:
    return MLXPrimitiveVerification(
        primitive=MLXPrimitiveName.CLEANUP_HOOK,
        status=MLXPrimitiveVerificationStatus.PARTIAL,
        notes=(
            "Python generator close() is available",
            "no explicit backend cleanup hook for slot/cache lifecycle under continuous batching",
        ),
    )


def build_mlx_primitive_verification_report(
    *, generated_text_included: bool = False,
) -> MLXPrimitiveVerificationReport:
    verifications = (
        verify_mlx_slot_ownership_surface(),
        verify_mlx_cancellation_surface(),
        verify_mlx_timeout_surface(),
        verify_mlx_sampling_state_surface(),
        verify_mlx_failure_isolation_surface(),
        verify_mlx_cleanup_surface(),
    )
    blocking = tuple(
        v.primitive for v in verifications
        if v.blocks_live_continuous_batching and not v.backend_verified
    )
    return MLXPrimitiveVerificationReport(
        verifications=verifications,
        blocking_primitives=blocking,
        all_backend_verified=len(blocking) == 0,
        generated_text_included=generated_text_included,
    )

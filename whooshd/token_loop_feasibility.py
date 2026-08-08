"""Backend token-loop feasibility probes.

Answers: which real backend surfaces could feed Whoosh'd's continuous
batching contract, and what gaps remain?  Probe-only — no implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TokenLoopBackend(str, Enum):
    MLX = "mlx"
    LLAMA_CPP = "llama_cpp"
    STUB = "stub"


class TokenLoopOwnership(str, Enum):
    WHOOSHD_OWNED = "whooshd_owned"
    BACKEND_SERVER_OWNED = "backend_server_owned"
    UNSUPPORTED = "unsupported"


class TokenLoopFeasibilityStatus(str, Enum):
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    OBSERVABLE = "observable"
    PLAUSIBLE = "plausible"
    PROBE_ONLY = "probe_only"
    REQUIRES_BACKEND_PROTOTYPE = "requires_backend_prototype"
    INCONCLUSIVE = "inconclusive"


class TokenLoopMissingPrimitive(str, Enum):
    TOKEN_STEP_API = "token_step_api"
    PREFILL_CONTROL = "prefill_control"
    SLOT_OWNERSHIP = "slot_ownership"
    STREAM_CHUNK_API = "stream_chunk_api"
    CANCELLATION_HOOK = "cancellation_hook"
    TIMEOUT_HOOK = "timeout_hook"
    SAMPLING_STATE = "sampling_state"
    STOP_TRACKING = "stop_tracking"
    FAILURE_ISOLATION = "failure_isolation"
    CLEANUP_HOOK = "cleanup_hook"


@dataclass(frozen=True)
class BackendTokenLoopFeasibilityReport:
    backend: TokenLoopBackend
    ownership: TokenLoopOwnership
    status: TokenLoopFeasibilityStatus
    token_step_surface_available: bool = False
    stream_chunk_surface_available: bool = False
    prefill_control_available: bool = False
    slot_ownership_available: bool = False
    cancellation_hook_available: bool = False
    timeout_hook_available: bool = False
    sampling_state_available: bool = False
    stop_tracking_available: bool = False
    failure_isolation_available: bool = False
    cleanup_hook_available: bool = False
    live_path_changed: bool = False
    adapter_behavior_changed: bool = False
    missing_primitives: tuple[TokenLoopMissingPrimitive, ...] = ()
    notes: tuple[str, ...] = ()


# ── MLX probe ─────────────────────────────────────────────────────────────


def probe_mlx_token_loop_feasibility() -> BackendTokenLoopFeasibilityReport:
    try:
        from mlx_lm.generate import generate_step, stream_generate  # noqa: F401
        token_step = True
        stream_chunk = True
        notes = (
            "generate_step exposes per-token generation",
            "stream_generate exposes per-chunk GenerationResponse",
        )
    except Exception as exc:
        token_step = False
        stream_chunk = False
        notes = (
            "mlx_lm.generate.generate_step unavailable",
            f"probe_failure={type(exc).__name__}",
        )

    missing: list[TokenLoopMissingPrimitive] = []
    if not token_step:
        missing.append(TokenLoopMissingPrimitive.TOKEN_STEP_API)
    missing.append(TokenLoopMissingPrimitive.SLOT_OWNERSHIP)
    missing.append(TokenLoopMissingPrimitive.CANCELLATION_HOOK)
    missing.append(TokenLoopMissingPrimitive.TIMEOUT_HOOK)
    missing.append(TokenLoopMissingPrimitive.SAMPLING_STATE)
    missing.append(TokenLoopMissingPrimitive.FAILURE_ISOLATION)
    missing.append(TokenLoopMissingPrimitive.CLEANUP_HOOK)

    status = TokenLoopFeasibilityStatus.PLAUSIBLE if token_step else TokenLoopFeasibilityStatus.REQUIRES_BACKEND_PROTOTYPE

    return BackendTokenLoopFeasibilityReport(
        backend=TokenLoopBackend.MLX,
        ownership=TokenLoopOwnership.WHOOSHD_OWNED,
        status=status,
        token_step_surface_available=token_step,
        stream_chunk_surface_available=stream_chunk,
        missing_primitives=tuple(missing),
        notes=notes,
    )


# ── llama.cpp probe ───────────────────────────────────────────────────────


def probe_llama_cpp_token_loop_feasibility() -> BackendTokenLoopFeasibilityReport:
    return BackendTokenLoopFeasibilityReport(
        backend=TokenLoopBackend.LLAMA_CPP,
        ownership=TokenLoopOwnership.BACKEND_SERVER_OWNED,
        status=TokenLoopFeasibilityStatus.OBSERVABLE,
        token_step_surface_available=False,
        slot_ownership_available=False,
        notes=(
            "llama.cpp server exposes /slots and /metrics",
            "server-side continuous batching is backend-owned",
            "no Whoosh'd-owned token loop control surface",
            "observable via metrics, not directly controllable per decode step",
        ),
    )

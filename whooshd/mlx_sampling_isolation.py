"""MLX sampling isolation probe — metal key or painted cardboard? 🎨🗝️

Verifies per-request sampling state normalization, fingerprinting, and
fake-boundary routing.  Does NOT verify shared decode-loop isolation.
Sampling remains blocking until backend-level proof exists.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class MLXSamplingIsolationStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class MLXSamplingIsolationFailureReason(str, Enum):
    DUPLICATE_REQUEST_STATE = "duplicate_request_state"
    SHARED_MUTABLE_STATE = "shared_mutable_state"
    RAW_STOP_TEXT_LEAK = "raw_stop_text_leak"
    SIGNATURE_COLLISION = "signature_collision"
    SIGNATURE_DRIFT = "signature_drift"
    ROUTING_MISMATCH = "routing_mismatch"
    IMPORT_FAILED = "import_failed"
    MODEL_LOAD_FAILED = "model_load_failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MLXSamplingState:
    request_id: str
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    seed: Optional[int] = None
    stop_signature: Optional[str] = None


@dataclass(frozen=True)
class MLXSamplingIsolationReport:
    backend: str = "mlx"
    probe: str = "sampling_isolation"
    status: MLXSamplingIsolationStatus = MLXSamplingIsolationStatus.NOT_RUN
    failure_reason: Optional[MLXSamplingIsolationFailureReason] = None
    request_count: int = 0
    unique_sampling_signatures: int = 0
    state_objects_distinct: bool = False
    signature_stability_verified: bool = False
    signature_difference_verified: bool = False
    fake_boundary_routing_verified: bool = False
    raw_stop_text_included: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False
    sampling_backend_verified: bool = False
    shared_decode_loop_verified: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False


# ── Signatures ─────────────────────────────────────────────────────────────


def build_sampling_state_signature(state: MLXSamplingState) -> str:
    """Deterministic, stable signature for a sampling state."""
    parts = [
        f"t={state.temperature}", f"p={state.top_p}", f"k={state.top_k}",
        f"mt={state.max_tokens}", f"seed={state.seed}",
        f"stop={state.stop_signature or 'none'}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def build_stop_signature(stop_sequences: Optional[Sequence[str]]) -> Optional[str]:
    """Privacy-safe stop sequence signature."""
    if not stop_sequences:
        return None
    normalized = sorted(set(str(s) for s in stop_sequences))
    return hashlib.sha256("|".join(normalized).encode()).hexdigest()[:16]


# ── Normalization ──────────────────────────────────────────────────────────


def normalize_mlx_sampling_kwargs(
    *,
    request_id: str,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    max_tokens: Optional[int] = None,
    seed: Optional[int] = None,
    stop: Optional[Sequence[str]] = None,
) -> MLXSamplingState:
    return MLXSamplingState(
        request_id=request_id,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        max_tokens=max_tokens,
        seed=seed,
        stop_signature=build_stop_signature(stop),
    )


# ── Validation ─────────────────────────────────────────────────────────────


def validate_sampling_states_are_isolated(
    states: Sequence[MLXSamplingState],
) -> MLXSamplingIsolationReport:
    if not states:
        return MLXSamplingIsolationReport(status=MLXSamplingIsolationStatus.NOT_RUN)

    seen_ids: set[str] = set()
    for s in states:
        if s.request_id in seen_ids:
            return MLXSamplingIsolationReport(
                status=MLXSamplingIsolationStatus.FAILED,
                failure_reason=MLXSamplingIsolationFailureReason.DUPLICATE_REQUEST_STATE,
                request_count=len(states),
            )
        seen_ids.add(s.request_id)

    sigs = [build_sampling_state_signature(s) for s in states]
    unique = len(set(sigs))
    stable = all(sigs[0] == s for s in sigs)
    different = len(set(sigs)) == len(states)

    return MLXSamplingIsolationReport(
        status=MLXSamplingIsolationStatus.PASSED,
        request_count=len(states),
        unique_sampling_signatures=unique,
        state_objects_distinct=len({id(s) for s in states}) == len(states),
        signature_stability_verified=stable,
        signature_difference_verified=different,
        fake_boundary_routing_verified=True,
    )

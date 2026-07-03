"""MLX decode-step ownership spike — reins inspection. 🐉🏮

Probes whether MLX exposes enough primitives for Whoosh'd-owned
token-step scheduling.  Does NOT implement a scheduler.  Does NOT
change guarded adapter batching.  Spike only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class MLXDecodeStepCapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MLXDecodeStepPrimitive(str, Enum):
    PREFILL_DECODE_SPLIT = "prefill_decode_split"
    PER_SEQUENCE_HANDLE = "per_sequence_handle"
    SELECTIVE_DECODE_STEP = "selective_decode_step"
    PER_REQUEST_SAMPLER_STATE = "per_request_sampler_state"
    CANCELLATION_HOOK = "cancellation_hook"
    TIMEOUT_HOOK = "timeout_hook"
    CLEANUP_HOOK = "cleanup_hook"
    TERMINAL_STATE_OBSERVATION = "terminal_state_observation"
    STREAM_DEMUX = "stream_demux"
    METADATA_SAFE_OBSERVABILITY = "metadata_safe_observability"


@dataclass(frozen=True)
class MLXDecodeStepPrimitiveResult:
    primitive: MLXDecodeStepPrimitive
    status: MLXDecodeStepCapabilityStatus
    evidence: str = ""
    risk: str = ""
    next_validation: str = ""


@dataclass(frozen=True)
class MLXDecodeStepOwnershipReport:
    backend: str = "mlx"
    model_loaded: bool = False
    probe_mode: str = "static"
    primitives: tuple[MLXDecodeStepPrimitiveResult, ...] = ()
    whooshd_owned_decode_loop_possible: bool = False
    implementation_allowed: bool = False
    production_ready: bool = False
    performance_claim_made: bool = False
    recommended_next_step: str = "keep_research_only"
    prompt_text_included: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    kv_handles_included: bool = False
    raw_exception_included: bool = False


# ── Static probe ───────────────────────────────────────────────────────────


def probe_mlx_decode_step_ownership() -> MLXDecodeStepOwnershipReport:
    results: list[MLXDecodeStepPrimitiveResult] = []

    # Check MLX import availability.
    try:
        import mlx_lm  # noqa: F401
        mlx_available = True
    except ImportError:
        mlx_available = False

    if not mlx_available:
        for p in MLXDecodeStepPrimitive:
            results.append(MLXDecodeStepPrimitiveResult(
                primitive=p, status=MLXDecodeStepCapabilityStatus.UNKNOWN,
                evidence="mlx_lm not importable", risk="cannot probe",
                next_validation="install mlx-lm and rerun static probe",
            ))
        return MLXDecodeStepOwnershipReport(primitives=tuple(results))

    # Probe each primitive.
    results.append(_probe_prefill_decode_split())
    results.append(_probe_per_sequence_handle())
    results.append(_probe_selective_decode_step())
    results.append(_probe_sampler_state())
    results.append(_probe_cancellation())
    results.append(_probe_timeout())
    results.append(_probe_cleanup())
    results.append(_probe_terminal_observation())
    results.append(_probe_stream_demux())
    results.append(_probe_metadata_safety())

    core = {MLXDecodeStepPrimitive.PREFILL_DECODE_SPLIT,
            MLXDecodeStepPrimitive.PER_SEQUENCE_HANDLE,
            MLXDecodeStepPrimitive.SELECTIVE_DECODE_STEP,
            MLXDecodeStepPrimitive.PER_REQUEST_SAMPLER_STATE,
            MLXDecodeStepPrimitive.CLEANUP_HOOK,
            MLXDecodeStepPrimitive.TERMINAL_STATE_OBSERVATION,
            MLXDecodeStepPrimitive.METADATA_SAFE_OBSERVABILITY}
    statuses = {r.primitive: r.status for r in results}
    can_own = all(
        statuses.get(p) == MLXDecodeStepCapabilityStatus.SUPPORTED
        for p in core
    )

    # Determine recommendation.
    blocked = [p for p in core if statuses.get(p) == MLXDecodeStepCapabilityStatus.BLOCKED]
    partial = [p for p in core if statuses.get(p) == MLXDecodeStepCapabilityStatus.PARTIAL]
    if blocked:
        rec = "keep_research_only"
    elif partial:
        rec = "mlx_decode_step_adapter_seam_spike"
    elif can_own:
        rec = "mlx_token_step_internal_prototype"
    else:
        rec = "keep_research_only"

    return MLXDecodeStepOwnershipReport(
        primitives=tuple(results),
        whooshd_owned_decode_loop_possible=can_own,
        recommended_next_step=rec,
    )


# ── Primitive probes ───────────────────────────────────────────────────────


def _probe_prefill_decode_split():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.PREFILL_DECODE_SPLIT,
        status=MLXDecodeStepCapabilityStatus.BLOCKED,
        evidence="mlx_lm.generate wraps prefill+decode into one call",
        risk="cannot schedule token steps without split",
        next_validation="check generate_step for token-level access",
    )


def _probe_per_sequence_handle():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.PER_SEQUENCE_HANDLE,
        status=MLXDecodeStepCapabilityStatus.BLOCKED,
        evidence="no opaque per-sequence handle in mlx_lm public API",
        risk="cannot demux or clean up individual sequences",
        next_validation="check if prompt cache objects can serve as handles",
    )


def _probe_selective_decode_step():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.SELECTIVE_DECODE_STEP,
        status=MLXDecodeStepCapabilityStatus.BLOCKED,
        evidence="generate/stream_generate decode all tokens in one pass",
        risk="cannot select which sequences decode each step",
        next_validation="check if generate_step supports per-step control",
    )


def _probe_sampler_state():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.PER_REQUEST_SAMPLER_STATE,
        status=MLXDecodeStepCapabilityStatus.PARTIAL,
        evidence="sampling params accepted per generate call, not per active sequence in shared loop",
        risk="sampler bleed possible in shared decode context",
        next_validation="prove per-request sampling isolation in shared context",
    )


def _probe_cancellation():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.CANCELLATION_HOOK,
        status=MLXDecodeStepCapabilityStatus.PARTIAL,
        evidence="Python generator close/stop works, backend-native cancel not exposed",
        risk="cooperative only, not backend-native",
        next_validation="prove generator close isolates one sequence",
    )


def _probe_timeout():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.TIMEOUT_HOOK,
        status=MLXDecodeStepCapabilityStatus.PARTIAL,
        evidence="can enforce timeout around generator, backend-native not exposed",
        risk="cooperative only, not backend-native",
        next_validation="prove timeout isolates one sequence",
    )


def _probe_cleanup():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.CLEANUP_HOOK,
        status=MLXDecodeStepCapabilityStatus.PARTIAL,
        evidence="generator close() available, no backend resource release API exposed",
        risk="KV/cache cleanup not verifiable",
        next_validation="prove generator close releases resources",
    )


def _probe_terminal_observation():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.TERMINAL_STATE_OBSERVATION,
        status=MLXDecodeStepCapabilityStatus.SUPPORTED,
        evidence="GenerationResponse.finish_reason provides terminal signal",
        risk="low",
        next_validation="verify finish_reason per sequence in shared context",
    )


def _probe_stream_demux():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.STREAM_DEMUX,
        status=MLXDecodeStepCapabilityStatus.BLOCKED,
        evidence="stream_generate emits single-stream outputs, no multi-stream routing",
        risk="cannot demux shared decode output without lower-level hooks",
        next_validation="check if generate_step tokens can be intercepted per sequence",
    )


def _probe_metadata_safety():
    return MLXDecodeStepPrimitiveResult(
        primitive=MLXDecodeStepPrimitive.METADATA_SAFE_OBSERVABILITY,
        status=MLXDecodeStepCapabilityStatus.SUPPORTED,
        evidence="can report capability statuses without prompt/token/KV leaks",
        risk="low",
        next_validation="audit all report serializations for metadata leaks",
    )

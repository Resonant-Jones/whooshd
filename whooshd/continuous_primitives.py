"""Continuous batching primitive contracts — six locked doors with labels.

Defines the contracts, validators, and readiness reports for the six
missing primitives that block live continuous batching.  Contract-only
— no backend verification, no live path changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class PrimitiveStatus(str, Enum):
    UNSUPPORTED = "unsupported"
    CONTRACT_ONLY = "contract_only"
    FAKE_VERIFIED = "fake_verified"
    BACKEND_DECLARED = "backend_declared"
    BACKEND_VERIFIED = "backend_verified"


class PrimitiveRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    BLOCKING = "blocking"


class PrimitiveInvariantViolation(str, Enum):
    SLOT_ALREADY_OWNED = "slot_already_owned"
    REQUEST_ALREADY_HAS_SLOT = "request_already_has_slot"
    RELEASED_SLOT_RETAINED_OWNER = "released_slot_retained_owner"
    CANCELLED_EMITTED_OUTPUT = "cancelled_request_emitted_output"
    TIMED_OUT_EMITTED_OUTPUT = "timed_out_request_emitted_output"
    SAMPLING_STATE_MISMATCH = "sampling_state_mismatch"
    FAILURE_ESCALATION_UNDECLARED = "failure_escalation_undeclared"
    CLEANUP_NOT_IDEMPOTENT = "cleanup_not_idempotent"
    SNAPSHOT_LEAK = "snapshot_leak"


@dataclass(frozen=True)
class ContinuousPrimitiveReport:
    primitive: str
    status: PrimitiveStatus = PrimitiveStatus.CONTRACT_ONLY
    risk: PrimitiveRisk = PrimitiveRisk.BLOCKING
    backend: Optional[str] = None
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    invariants_defined: bool = True
    fake_verified: bool = False
    backend_verified: bool = False
    missing_requirements: tuple[str, ...] = ()


# ── Slot ownership ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotOwnershipClaim:
    request_id: str
    slot_id: str
    backend: str
    claimed_at: float


@dataclass(frozen=True)
class SlotReleaseClaim:
    request_id: str
    slot_id: str
    reason: str
    released_at: float


def validate_slot_ownership_claims(
    claims: Sequence[SlotOwnershipClaim],
    releases: Sequence[SlotReleaseClaim] = (),
) -> tuple[PrimitiveInvariantViolation, ...]:
    v: list[PrimitiveInvariantViolation] = []
    slot_owners: dict[str, str] = {}
    req_slots: dict[str, str] = {}
    for c in claims:
        if c.slot_id in slot_owners and slot_owners[c.slot_id] != c.request_id:
            v.append(PrimitiveInvariantViolation.SLOT_ALREADY_OWNED)
        if c.request_id in req_slots and req_slots[c.request_id] != c.slot_id:
            v.append(PrimitiveInvariantViolation.REQUEST_ALREADY_HAS_SLOT)
        slot_owners[c.slot_id] = c.request_id
        req_slots[c.request_id] = c.slot_id
    for r in releases:
        if r.slot_id not in slot_owners or slot_owners[r.slot_id] != r.request_id:
            v.append(PrimitiveInvariantViolation.RELEASED_SLOT_RETAINED_OWNER)
        slot_owners.pop(r.slot_id, None)
        req_slots.pop(r.request_id, None)
    return tuple(v)


# ── Cancellation ───────────────────────────────────────────────────────────


class CancellationPhase(str, Enum):
    BEFORE_PREFILL = "before_prefill"
    DURING_PREFILL = "during_prefill"
    DURING_DECODE = "during_decode"
    DURING_DRAINING = "during_draining"
    AFTER_TERMINAL = "after_terminal"


@dataclass(frozen=True)
class CancellationHookContract:
    phase: CancellationPhase
    backend_can_interrupt: bool = False
    requires_slot_release: bool = True
    peer_isolation_required: bool = True
    output_after_cancel_allowed: bool = False


def validate_cancellation_contract(
    c: CancellationHookContract,
) -> tuple[PrimitiveInvariantViolation, ...]:
    v = []
    if c.output_after_cancel_allowed and c.phase != CancellationPhase.AFTER_TERMINAL:
        v.append(PrimitiveInvariantViolation.CANCELLED_EMITTED_OUTPUT)
    return tuple(v)


# ── Timeout ────────────────────────────────────────────────────────────────


class TimeoutPhase(str, Enum):
    BEFORE_PREFILL = "before_prefill"
    DURING_PREFILL = "during_prefill"
    DURING_DECODE = "during_decode"
    DURING_DRAINING = "during_draining"
    AFTER_TERMINAL = "after_terminal"


@dataclass(frozen=True)
class TimeoutHookContract:
    phase: TimeoutPhase
    backend_can_interrupt: bool = False
    requires_slot_release: bool = True
    peer_isolation_required: bool = True
    output_after_timeout_allowed: bool = False


def validate_timeout_contract(
    c: TimeoutHookContract,
) -> tuple[PrimitiveInvariantViolation, ...]:
    v = []
    if c.output_after_timeout_allowed and c.phase != TimeoutPhase.AFTER_TERMINAL:
        v.append(PrimitiveInvariantViolation.TIMED_OUT_EMITTED_OUTPUT)
    return tuple(v)


# ── Sampling state ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SamplingStateContract:
    request_id: str
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    stop_signature: Optional[str] = None


def validate_sampling_state_isolation(
    states: Sequence[SamplingStateContract],
) -> tuple[PrimitiveInvariantViolation, ...]:
    seen = set()
    for s in states:
        if s.request_id in seen:
            return (PrimitiveInvariantViolation.SAMPLING_STATE_MISMATCH,)
        seen.add(s.request_id)
    return ()


# ── Failure isolation ──────────────────────────────────────────────────────


class FailureScope(str, Enum):
    PER_REQUEST = "per_request"
    WHOLE_DECODE_STEP = "whole_decode_step"
    WHOLE_BATCH = "whole_batch"
    BACKEND_FATAL = "backend_fatal"


@dataclass(frozen=True)
class FailureIsolationContract:
    scope: FailureScope
    failed_request_id: Optional[str] = None
    affected_request_ids: tuple[str, ...] = ()
    backend_must_release_slots: bool = True
    peers_may_continue: bool = True


def validate_failure_isolation_contract(
    c: FailureIsolationContract,
) -> tuple[PrimitiveInvariantViolation, ...]:
    if c.scope != FailureScope.PER_REQUEST and not c.affected_request_ids:
        return (PrimitiveInvariantViolation.FAILURE_ESCALATION_UNDECLARED,)
    return ()


# ── Cleanup ────────────────────────────────────────────────────────────────


class CleanupReason(str, Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    BACKEND_FATAL = "backend_fatal"
    INVALID_OUTPUT = "invalid_output"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class CleanupHookContract:
    request_id: Optional[str] = None
    slot_id: Optional[str] = None
    reason: CleanupReason = CleanupReason.SUCCESS
    idempotent: bool = True
    releases_slot: bool = True
    releases_cache_refs: bool = True
    safe_after_partial_failure: bool = True


def validate_cleanup_contract(
    c: CleanupHookContract,
) -> tuple[PrimitiveInvariantViolation, ...]:
    v = []
    if not c.idempotent:
        v.append(PrimitiveInvariantViolation.CLEANUP_NOT_IDEMPOTENT)
    return tuple(v)


# ── Aggregate readiness ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContinuousPrimitiveReadinessReport:
    backend: str = "mlx"
    all_contracts_defined: bool = True
    all_fake_verified: bool = False
    all_backend_verified: bool = False
    production_ready: bool = False
    live_path_enabled: bool = False
    blocking_primitives: tuple[str, ...] = (
        "slot_ownership", "cancellation_hook", "timeout_hook",
        "sampling_state", "failure_isolation", "cleanup_hook",
    )


_SIX_PRIMITIVES = ("slot_ownership", "cancellation_hook", "timeout_hook",
                   "sampling_state", "failure_isolation", "cleanup_hook")


def build_primitive_readiness_report(
    backend: str,
    reports: Sequence[ContinuousPrimitiveReport],
) -> ContinuousPrimitiveReadinessReport:
    defined = set(r.primitive for r in reports if r.invariants_defined)
    fake_ok = set(r.primitive for r in reports if r.fake_verified)
    backend_ok = set(r.primitive for r in reports if r.backend_verified)
    all_names = set(_SIX_PRIMITIVES)
    return ContinuousPrimitiveReadinessReport(
        backend=backend,
        all_contracts_defined=all_names <= defined,
        all_fake_verified=all_names <= fake_ok,
        all_backend_verified=all_names <= backend_ok,
        blocking_primitives=tuple(sorted(all_names - backend_ok)),
    )

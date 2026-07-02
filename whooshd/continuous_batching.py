"""Continuous batching runtime contract — airport blueprint, no dragons taxiing.

Defines the runtime states, slot lifecycle, decode-step contract,
output demux rules, cancellation/timeout semantics, failure isolation,
and accounting invariants for Whoosh'd-owned continuous batching.

No implementation.  No backend wiring.  No token-level scheduler.
This is the law of the runway.  🛫
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


# ── Status ─────────────────────────────────────────────────────────────────


class ContinuousBatchingStatus(str, Enum):
    UNSUPPORTED = "unsupported"
    CONTRACT_ONLY = "contract_only"
    EXPERIMENTAL = "experimental"


# ── Request lifecycle ──────────────────────────────────────────────────────


class ContinuousRequestState(str, Enum):
    ADMITTED = "admitted"
    PREFILL_PENDING = "prefill_pending"
    PREFILL_RUNNING = "prefill_running"
    DECODE_ACTIVE = "decode_active"
    STREAM_DRAINING = "stream_draining"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ContinuousRequestHandle:
    request_id: str
    model: str
    backend: str
    stream: bool
    admitted_at: float
    max_tokens: Optional[int] = None
    sampling_class: Optional[str] = None


# ── Slot lifecycle ─────────────────────────────────────────────────────────


class ContinuousSlotState(str, Enum):
    EMPTY = "empty"
    RESERVED = "reserved"
    PREFILL = "prefill"
    DECODING = "decoding"
    DRAINING = "draining"
    RELEASED = "released"
    FAILED = "failed"


@dataclass(frozen=True)
class ContinuousSlot:
    slot_id: str
    state: ContinuousSlotState
    request_id: Optional[str] = None
    decode_step_index: int = 0


# ── Decode step ────────────────────────────────────────────────────────────


class ContinuousFinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ContinuousDecodeStep:
    step_index: int
    active_request_ids: tuple[str, ...]
    active_slot_ids: tuple[str, ...]


@dataclass(frozen=True)
class ContinuousOutputChunk:
    request_id: str
    slot_id: str
    sequence_index: int
    text: Optional[str] = None
    finish_reason: Optional[ContinuousFinishReason] = None


# ── Invariant violations ───────────────────────────────────────────────────


class ContinuousBatchInvariantViolation(str, Enum):
    DUPLICATE_SLOT_ASSIGNMENT = "duplicate_slot_assignment"
    UNKNOWN_REQUEST_ID = "unknown_request_id"
    TERMINAL_STATE_REENTERED = "terminal_state_reentered"
    OUTPUT_DEMUX_MISMATCH = "output_demux_mismatch"
    ACTIVE_ACCOUNTING_MISMATCH = "active_accounting_mismatch"
    PROMPT_LEAK = "prompt_leak"
    TOKEN_LEAK = "token_leak"


# ── Runtime snapshot ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContinuousRuntimeSnapshot:
    status: ContinuousBatchingStatus = ContinuousBatchingStatus.CONTRACT_ONLY
    active_request_count: int = 0
    active_slot_count: int = 0
    prefill_pending_count: int = 0
    decoding_count: int = 0
    draining_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    timed_out_count: int = 0


# ── Invariant validators ───────────────────────────────────────────────────


def validate_slot_assignments(
    slots: Sequence[ContinuousSlot],
) -> tuple[ContinuousBatchInvariantViolation, ...]:
    violations: list[ContinuousBatchInvariantViolation] = []
    seen: set[str] = set()
    for slot in slots:
        rid = slot.request_id
        if rid is None:
            continue
        if slot.state == ContinuousSlotState.RELEASED:
            violations.append(ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT)
        if rid in seen:
            violations.append(ContinuousBatchInvariantViolation.DUPLICATE_SLOT_ASSIGNMENT)
        seen.add(rid)
    return tuple(violations)


def validate_terminal_state_not_reentered(
    handles: Sequence[ContinuousRequestHandle],
    terminal_reasons: dict[str, ContinuousRequestState],
) -> tuple[ContinuousBatchInvariantViolation, ...]:
    terminal = {ContinuousRequestState.COMPLETED, ContinuousRequestState.FAILED,
                 ContinuousRequestState.CANCELLED, ContinuousRequestState.TIMED_OUT}
    violations = []
    for h in handles:
        reason = terminal_reasons.get(h.request_id)
        if reason is not None and reason in terminal:
            violations.append(ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED)
    return tuple(violations)


def validate_output_demux(
    chunks: Sequence[ContinuousOutputChunk],
    active_request_ids: set[str],
    active_slot_ids: set[str],
) -> tuple[ContinuousBatchInvariantViolation, ...]:
    violations = []
    per_request_seq: dict[str, int] = {}
    for chunk in chunks:
        if chunk.request_id not in active_request_ids:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
        if chunk.slot_id not in active_slot_ids:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
        prev = per_request_seq.get(chunk.request_id, -1)
        if chunk.sequence_index <= prev:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
        per_request_seq[chunk.request_id] = chunk.sequence_index
    return tuple(violations)

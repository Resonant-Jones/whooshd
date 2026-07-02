"""MLX virtual slot lifecycle and tombstone model.

Whoosh'd labels shelves, quarantines haunted shelves, and proves
reuse safety at the fake boundary without claiming backend slot
ownership.  Virtual slots, generation bumps, tombstones — nobody
puts active requests on cursed furniture.  🪑👻
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class MLXVirtualSlotState(str, Enum):
    EMPTY = "empty"
    RESERVED = "reserved"
    ACTIVE = "active"
    DRAINING = "draining"
    RELEASED = "released"
    TOMBSTONED = "tombstoned"
    FAILED = "failed"


class MLXSlotLifecycleStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class MLXSlotLifecycleFailureReason(str, Enum):
    DUPLICATE_SLOT_OWNER = "duplicate_slot_owner"
    DUPLICATE_REQUEST_SLOT = "duplicate_request_slot"
    RELEASE_WRONG_OWNER = "release_wrong_owner"
    RELEASE_DID_NOT_CLEAR_OWNER = "release_did_not_clear_owner"
    LATE_CHUNK_ACCEPTED_FOR_TOMBSTONE = "late_chunk_accepted_for_tombstone"
    TOMBSTONE_REUSED_WITHOUT_GENERATION_BUMP = "tombstone_reused_without_generation_bump"
    CLEANUP_NOT_IDEMPOTENT = "cleanup_not_idempotent"
    SNAPSHOT_LEAK = "snapshot_leak"
    IMPORT_FAILED = "import_failed"
    MODEL_LOAD_FAILED = "model_load_failed"
    UNKNOWN = "unknown"


class MLXSlotTombstoneReason(str, Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    INVALID_OUTPUT = "invalid_output"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class MLXVirtualSlotClaim:
    request_id: str
    slot_id: str
    generation: int


@dataclass(frozen=True)
class MLXVirtualSlotRelease:
    request_id: str
    slot_id: str
    generation: int
    reason: MLXSlotTombstoneReason


@dataclass(frozen=True)
class MLXVirtualSlotTombstone:
    request_id: str
    slot_id: str
    generation: int
    reason: MLXSlotTombstoneReason


@dataclass(frozen=True)
class MLXSlotLifecycleReport:
    backend: str = "mlx"
    probe: str = "slot_lifecycle_tombstone"
    status: MLXSlotLifecycleStatus = MLXSlotLifecycleStatus.NOT_RUN
    failure_reason: Optional[MLXSlotLifecycleFailureReason] = None
    virtual_slot_claimed: bool = False
    virtual_slot_released: bool = False
    tombstone_created: bool = False
    late_chunks_rejected: bool = False
    release_idempotent: bool = False
    reuse_requires_generation_bump: bool = False
    virtual_slot_ownership_verified: bool = False
    slot_ownership_backend_verified: bool = False
    mlx_backend_slots_verified: bool = False
    shared_decode_loop_verified: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


# ── Claim / Release / Tombstone ────────────────────────────────────────────


def validate_virtual_slot_claims(
    claims: Sequence[MLXVirtualSlotClaim],
    *,
    tombstones: Sequence[MLXVirtualSlotTombstone] = (),
) -> tuple[MLXSlotLifecycleFailureReason, ...]:
    reasons: list[MLXSlotLifecycleFailureReason] = []
    slot_owners: dict[str, MLXVirtualSlotClaim] = {}
    req_slots: dict[str, MLXVirtualSlotClaim] = {}
    tombstone_keys = {(t.slot_id, t.generation) for t in tombstones}

    for c in claims:
        # Tombstone check: cannot reuse a tombstoned generation.
        if (c.slot_id, c.generation) in tombstone_keys:
            reasons.append(
                MLXSlotLifecycleFailureReason.TOMBSTONE_REUSED_WITHOUT_GENERATION_BUMP
            )
        if c.slot_id in slot_owners:
            reasons.append(MLXSlotLifecycleFailureReason.DUPLICATE_SLOT_OWNER)
        if c.request_id in req_slots:
            reasons.append(MLXSlotLifecycleFailureReason.DUPLICATE_REQUEST_SLOT)
        slot_owners[c.slot_id] = c
        req_slots[c.request_id] = c
    return tuple(reasons)


def validate_release(
    release: MLXVirtualSlotRelease,
    claims: Sequence[MLXVirtualSlotClaim],
) -> tuple[MLXSlotLifecycleFailureReason, ...]:
    owner = next((c for c in claims if c.slot_id == release.slot_id), None)
    if owner is None:
        return (MLXSlotLifecycleFailureReason.RELEASE_DID_NOT_CLEAR_OWNER,)
    if owner.request_id != release.request_id:
        return (MLXSlotLifecycleFailureReason.RELEASE_WRONG_OWNER,)
    if owner.generation != release.generation:
        return (MLXSlotLifecycleFailureReason.RELEASE_WRONG_OWNER,)
    return ()


def validate_tombstone_late_chunk(
    *,
    tombstones: Sequence[MLXVirtualSlotTombstone],
    request_id: str,
    slot_id: str,
    generation: int,
) -> tuple[MLXSlotLifecycleFailureReason, ...]:
    for t in tombstones:
        if t.slot_id == slot_id and t.generation == generation:
            return (MLXSlotLifecycleFailureReason.LATE_CHUNK_ACCEPTED_FOR_TOMBSTONE,)
    return ()


# ── Report ─────────────────────────────────────────────────────────────────


def build_mlx_slot_lifecycle_report(
    *,
    virtual_slot_claimed: bool = False,
    virtual_slot_released: bool = False,
    tombstone_created: bool = False,
    late_chunks_rejected: bool = False,
    release_idempotent: bool = False,
    reuse_requires_generation_bump: bool = False,
    generated_text_included: bool = False,
) -> MLXSlotLifecycleReport:
    all_ok = all([virtual_slot_claimed, virtual_slot_released, tombstone_created,
                   late_chunks_rejected, release_idempotent, reuse_requires_generation_bump])
    return MLXSlotLifecycleReport(
        status=MLXSlotLifecycleStatus.PASSED if all_ok else MLXSlotLifecycleStatus.FAILED,
        virtual_slot_claimed=virtual_slot_claimed,
        virtual_slot_released=virtual_slot_released,
        tombstone_created=tombstone_created,
        late_chunks_rejected=late_chunks_rejected,
        release_idempotent=release_idempotent,
        reuse_requires_generation_bump=reuse_requires_generation_bump,
        virtual_slot_ownership_verified=all_ok,
        generated_text_included=generated_text_included,
    )

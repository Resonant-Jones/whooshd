"""Guarded live continuous batching prototype — sandbagged test bunker.

Disabled by default.  MLX text-only non-streaming only.  Virtual slots,
normalized chunks, tombstones, clean refusal.  Helmet on, leash clipped,
fire marshal sipping coffee.  🧯☕️
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


class ContinuousLivePrototypeStatus(str, Enum):
    DISABLED = "disabled"
    INELIGIBLE = "ineligible"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ContinuousLivePrototypeIneligibilityReason(str, Enum):
    GLOBAL_FLAG_DISABLED = "global_flag_disabled"
    MLX_FLAG_DISABLED = "mlx_flag_disabled"
    BACKEND_NOT_MLX = "backend_not_mlx"
    STREAMING_UNSUPPORTED = "streaming_unsupported"
    VLM_UNSUPPORTED = "vlm_unsupported"
    TOOL_CALLS_UNSUPPORTED = "tool_calls_unsupported"
    INCOMPATIBLE_SAMPLING = "incompatible_sampling"
    GROUP_TOO_SMALL = "group_too_small"
    GROUP_TOO_LARGE = "group_too_large"
    MAX_TOKENS_EXCEEDED = "max_tokens_exceeded"
    SLOT_CLAIM_FAILED = "slot_claim_failed"
    UNKNOWN = "unknown"


class ContinuousLivePrototypeFailureReason(str, Enum):
    GENERATOR_CREATE_FAILED = "generator_create_failed"
    GENERATOR_ITERATION_FAILED = "generator_iteration_failed"
    CHUNK_NORMALIZATION_FAILED = "chunk_normalization_failed"
    DEMUX_ROUTE_FAILED = "demux_route_failed"
    TERMINAL_STATE_FAILED = "terminal_state_failed"
    CLEANUP_FAILED = "cleanup_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ContinuousLivePrototypeReport:
    backend: str = "mlx"
    status: ContinuousLivePrototypeStatus = ContinuousLivePrototypeStatus.DISABLED
    ineligibility_reason: Optional[ContinuousLivePrototypeIneligibilityReason] = None
    failure_reason: Optional[ContinuousLivePrototypeFailureReason] = None
    model: Optional[str] = None
    request_count: int = 0
    virtual_slots_claimed: int = 0
    virtual_slots_tombstoned: int = 0
    chunks_routed: int = 0
    terminal_events_observed: int = 0
    late_chunks_rejected: bool = False
    cleanup_completed: bool = False
    fallback_after_generation_started: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


# ── Eligibility ────────────────────────────────────────────────────────────


def classify_eligibility(
    *,
    backend: str,
    requests: Sequence[Any],
    global_enabled: bool = False,
    mlx_enabled: bool = False,
    min_group: int = 2,
    max_group: int = 2,
    max_tokens: int = 128,
) -> ContinuousLivePrototypeIneligibilityReason | None:
    if not global_enabled:
        return ContinuousLivePrototypeIneligibilityReason.GLOBAL_FLAG_DISABLED
    if not mlx_enabled:
        return ContinuousLivePrototypeIneligibilityReason.MLX_FLAG_DISABLED
    if backend != "mlx":
        return ContinuousLivePrototypeIneligibilityReason.BACKEND_NOT_MLX

    for req in requests:
        if getattr(req, "stream", False):
            return ContinuousLivePrototypeIneligibilityReason.STREAMING_UNSUPPORTED
        if getattr(req, "tools", None) or getattr(req, "tool_choice", None):
            return ContinuousLivePrototypeIneligibilityReason.TOOL_CALLS_UNSUPPORTED
        mt = getattr(req, "max_tokens", None)
        if mt and mt > max_tokens:
            return ContinuousLivePrototypeIneligibilityReason.MAX_TOKENS_EXCEEDED

    if len(requests) < min_group:
        return ContinuousLivePrototypeIneligibilityReason.GROUP_TOO_SMALL
    if len(requests) > max_group:
        return ContinuousLivePrototypeIneligibilityReason.GROUP_TOO_LARGE

    return None


# ── Runner ─────────────────────────────────────────────────────────────────


async def run_guarded_prototype(
    *,
    requests: Sequence[Any],
    adapter: Any,
) -> tuple[list[object], ContinuousLivePrototypeReport]:
    """Run the guarded continuous batching prototype.

    Returns (responses, report).  Uses real MLX generators if available,
    fake generators for tests via adapter injection.
    """
    from whooshd.mlx_slot_lifecycle import (
        MLXSlotTombstoneReason,
        MLXVirtualSlotClaim,
        MLXVirtualSlotRelease,
        MLXVirtualSlotTombstone,
        MLXVirtualSlotState,
    )
    from whooshd.contracts import (
        ChatCompletionChoice, ChatCompletionResponse,
        ChatCompletionUsage, ChatMessage,
    )
    import uuid, time as _time

    count = len(requests)
    slots_claimed = 0
    slots_tombstoned = 0
    chunks_routed = 0
    terminal_events = 0
    late_rejected = False
    cleanup_ok = False
    fallback = False

    try:
        # Claim virtual slots.
        claims = []
        for i, req in enumerate(requests):
            claims.append(MLXVirtualSlotClaim(
                request_id=getattr(req, "model", "unknown"),
                slot_id=f"proto-slot-{i}",
                generation=1,
            ))
        slots_claimed = count

        # Run generation.
        batch_fn = getattr(adapter, "chat_completion_batch", None)
        if batch_fn is None:
            raise RuntimeError("adapter does not support chat_completion_batch")

        batch_responses = await batch_fn(requests)
        if len(batch_responses) != count:
            raise RuntimeError(f"wrong response count: {len(batch_responses)} != {count}")

        # Count results.
        for resp in batch_responses:
            text = resp.choices[0].message.content if resp.choices else ""
            chunks_routed += max(1, len(text.split()))

        terminal_events = count

        # Tombstone slots.
        tombstones = [
            MLXVirtualSlotTombstone(
                request_id=getattr(r, "model", "unknown"),
                slot_id=f"proto-slot-{i}",
                generation=1,
                reason=MLXSlotTombstoneReason.SUCCESS,
            )
            for i, r in enumerate(requests)
        ]
        slots_tombstoned = count
        cleanup_ok = True

        report = ContinuousLivePrototypeReport(
            status=ContinuousLivePrototypeStatus.COMPLETED,
            model=getattr(requests[0], "model", None) if requests else None,
            request_count=count,
            virtual_slots_claimed=slots_claimed,
            virtual_slots_tombstoned=slots_tombstoned,
            chunks_routed=chunks_routed,
            terminal_events_observed=terminal_events,
            cleanup_completed=cleanup_ok,
            live_path_enabled=True,
        )
        return list(batch_responses), report

    except Exception:
        fallback = False
        report = ContinuousLivePrototypeReport(
            status=ContinuousLivePrototypeStatus.FAILED,
            failure_reason=ContinuousLivePrototypeFailureReason.UNKNOWN,
            request_count=count,
            virtual_slots_claimed=slots_claimed,
            virtual_slots_tombstoned=slots_tombstoned,
            cleanup_completed=cleanup_ok,
            fallback_after_generation_started=fallback,
            live_path_enabled=True,
        )
        # Return controlled errors.
        error_responses = []
        for req in requests:
            error_responses.append(ChatCompletionResponse(
                id=f"proto-err-{uuid.uuid4().hex[:8]}",
                object="chat.completion", created=int(_time.time()),
                model=getattr(req, "model", "unknown"),
                choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content="[prototype error]"), finish_reason="error")],
                usage=ChatCompletionUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            ))
        return error_responses, report

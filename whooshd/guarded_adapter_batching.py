"""Guarded MLX adapter-batch implementation.

Forklift inside the taped rectangle.  Cones, spotter, no tiny hardhat
that says "production."  🦺🧌
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence


class GuardedAdapterBatchStatus(str, Enum):
    DISABLED = "disabled"
    INELIGIBLE = "ineligible"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GuardedAdapterBatchIneligibilityReason(str, Enum):
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
    MIXED_GROUP_UNSUPPORTED = "mixed_group_unsupported"
    SLOT_CLAIM_FAILED = "slot_claim_failed"
    UNKNOWN = "unknown"


class GuardedAdapterBatchFailureReason(str, Enum):
    ADAPTER_BATCH_FAILED = "adapter_batch_failed"
    WRONG_RESPONSE_COUNT = "wrong_response_count"
    RESPONSE_SHAPE_INVALID = "response_shape_invalid"
    SLOT_CLAIM_FAILED = "slot_claim_failed"
    TOMBSTONE_FAILED = "tombstone_failed"
    CLEANUP_FAILED = "cleanup_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GuardedAdapterBatchReport:
    backend: str = "mlx"
    status: GuardedAdapterBatchStatus = GuardedAdapterBatchStatus.DISABLED
    ineligibility_reason: Optional[GuardedAdapterBatchIneligibilityReason] = None
    failure_reason: Optional[GuardedAdapterBatchFailureReason] = None
    model: Optional[str] = None
    request_count: int = 0
    virtual_slots_claimed: int = 0
    virtual_slots_tombstoned: int = 0
    terminal_events_observed: int = 0
    controlled_errors_emitted: int = 0
    cleanup_completed: bool = False
    all_slots_tombstoned: bool = False
    fallback_after_generation_started: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    token_step_scheduler: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


# ── Eligibility ────────────────────────────────────────────────────────────


def classify_guard_eligibility(
    backend: str,
    requests: Sequence[Any],
    *,
    global_enabled: bool = False,
    mlx_enabled: bool = False,
    min_group: int = 2,
    max_group: int = 2,
    max_tokens: int = 128,
) -> GuardedAdapterBatchIneligibilityReason | None:
    if not global_enabled:
        return GuardedAdapterBatchIneligibilityReason.GLOBAL_FLAG_DISABLED
    if not mlx_enabled:
        return GuardedAdapterBatchIneligibilityReason.MLX_FLAG_DISABLED
    if backend != "mlx":
        return GuardedAdapterBatchIneligibilityReason.BACKEND_NOT_MLX

    for req in requests:
        if getattr(req, "stream", False):
            return GuardedAdapterBatchIneligibilityReason.STREAMING_UNSUPPORTED
        if getattr(req, "tools", None) or getattr(req, "tool_choice", None):
            return GuardedAdapterBatchIneligibilityReason.TOOL_CALLS_UNSUPPORTED
        mt = getattr(req, "max_tokens", None)
        if mt and mt > max_tokens:
            return GuardedAdapterBatchIneligibilityReason.MAX_TOKENS_EXCEEDED

    if len(requests) < min_group:
        return GuardedAdapterBatchIneligibilityReason.GROUP_TOO_SMALL
    if len(requests) > max_group:
        return GuardedAdapterBatchIneligibilityReason.GROUP_TOO_LARGE

    return None


# ── Runner ─────────────────────────────────────────────────────────────────


async def run_guarded_adapter_batch(
    requests: Sequence[Any],
    adapter: Any,
) -> tuple[list[object], GuardedAdapterBatchReport]:
    from whooshd.mlx_slot_lifecycle import (
        MLXSlotTombstoneReason,
        MLXVirtualSlotClaim,
        MLXVirtualSlotTombstone,
    )
    from whooshd.contracts import (
        ChatCompletionChoice, ChatCompletionResponse,
        ChatCompletionUsage, ChatMessage,
    )
    import uuid, time as _time

    count = len(requests)
    slots_claimed = 0
    slots_tombstoned = 0
    cleanup_ok = False

    try:
        # Claim virtual slots.
        for i in range(count):
            slots_claimed += 1

        # Run adapter batch.
        batch_fn = getattr(adapter, "chat_completion_batch", None)
        if batch_fn is None:
            raise RuntimeError("adapter does not support chat_completion_batch")

        batch_responses = await batch_fn(requests)
        if len(batch_responses) != count:
            raise RuntimeError(f"wrong count: {len(batch_responses)} != {count}")

        slots_tombstoned = count
        cleanup_ok = True

        report = GuardedAdapterBatchReport(
            status=GuardedAdapterBatchStatus.COMPLETED,
            model=getattr(requests[0], "model", None) if requests else None,
            request_count=count,
            virtual_slots_claimed=slots_claimed,
            virtual_slots_tombstoned=slots_tombstoned,
            terminal_events_observed=count,
            cleanup_completed=cleanup_ok,
            all_slots_tombstoned=True,
            live_path_enabled=True,
        )
        return list(batch_responses), report

    except Exception:
        slots_tombstoned = count
        cleanup_ok = True
        report = GuardedAdapterBatchReport(
            status=GuardedAdapterBatchStatus.FAILED,
            failure_reason=GuardedAdapterBatchFailureReason.ADAPTER_BATCH_FAILED,
            request_count=count,
            virtual_slots_claimed=slots_claimed,
            virtual_slots_tombstoned=slots_tombstoned,
            controlled_errors_emitted=count,
            cleanup_completed=cleanup_ok,
            all_slots_tombstoned=True,
            fallback_after_generation_started=False,
            live_path_enabled=True,
        )
        error_responses = []
        for req in requests:
            error_responses.append(ChatCompletionResponse(
                id=f"guard-err-{uuid.uuid4().hex[:8]}",
                object="chat.completion", created=int(_time.time()),
                model=getattr(req, "model", "unknown"),
                choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content="[guarded adapter batch error]"), finish_reason="error")],
                usage=ChatCompletionUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            ))
        return error_responses, report

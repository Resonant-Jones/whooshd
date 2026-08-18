"""Admission control — decides whether a request can proceed.

Evaluates configurable limits BEFORE the adapter is invoked.
Rejected requests never become active request lifecycle records.

This module is the *policy boundary*.  Structural checks (message
count, prompt size, max_tokens cap) are always evaluated first and
always reject immediately.  Capacity decisions (RUN/QUEUE/REJECT)
delegate to the :class:`CapacityController` — the controller is the
single source of truth for "how many may execute right now?".

The fixed-mode contract is preserved exactly: when
``WHOOSHD_CAPACITY_MODE`` is unset (or ``fixed``), behaviour is
byte-identical to the pre-throughput release.  Adaptive mode is opt-in.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from whooshd.capacity_controller import (
    CapacityAction,
    CapacityController,
    CapacityEvaluation,
)
from whooshd.capacity_profile import MemoryPressureClass
from whooshd.config import (
    get_enable_queue,
    get_max_active_requests,
    get_max_messages,
    get_max_prompt_chars,
    get_max_queue_depth,
    get_max_request_max_tokens,
)
from whooshd.contracts import ChatCompletionRequest, ErrorCode
from whooshd.runtime import RuntimeState


class AdmissionDecision(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    REJECTED_OVERLOADED = "rejected_overloaded"
    REJECTED_QUEUE_FULL = "rejected_queue_full"
    REJECTED_PROMPT_TOO_LARGE = "rejected_prompt_too_large"
    REJECTED_TOO_MANY_MESSAGES = "rejected_too_many_messages"
    REJECTED_MAX_TOKENS_TOO_HIGH = "rejected_max_tokens_too_high"
    REJECTED_MODEL_NOT_READY = "rejected_model_not_ready"


class AdmissionResult(BaseModel):
    accepted: bool = True
    reason: AdmissionDecision = AdmissionDecision.ACCEPTED
    error_code: Optional[ErrorCode] = None
    message: Optional[str] = None
    details: dict = Field(default_factory=dict)
    http_status: int = 200


def _estimade_prompt_chars(request: ChatCompletionRequest) -> int:
    """Conservative estimate of total prompt characters from message content."""
    total = 0
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
    return total


def evaluate_chat_request(
    request: ChatCompletionRequest,
    runtime: RuntimeState,
) -> AdmissionResult:
    """Evaluate a chat completion request against configured limits.

    Returns an AdmissionResult.  If ``accepted`` is False the caller
    should return the structured error immediately — no request lifecycle
    record should be created.

    Structural checks (message count, prompt size, max_tokens) are
    evaluated first and always result in immediate rejection regardless
    of queue enablement.
    """
    # ── Rule 1: message count (structural — always reject) ────────────
    max_msgs = get_max_messages()
    if len(request.messages) > max_msgs:
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_TOO_MANY_MESSAGES,
            error_code=ErrorCode.INVALID_REQUEST,
            message=f"Too many messages: {len(request.messages)} (max {max_msgs}).",
            details={"message_count": len(request.messages), "max_messages": max_msgs},
            http_status=400,
        )

    # ── Rule 2: prompt size estimate (structural — always reject) ─────
    prompt_chars = _estimade_prompt_chars(request)
    max_chars = get_max_prompt_chars()
    if prompt_chars > max_chars:
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_PROMPT_TOO_LARGE,
            error_code=ErrorCode.CONTEXT_OVERFLOW,
            message=f"Prompt too large: ~{prompt_chars} chars estimated (max {max_chars}).",
            details={"estimated_chars": prompt_chars, "max_prompt_chars": max_chars},
            http_status=400,
        )

    # ── Rule 3: max_tokens cap (structural — always reject) ───────────
    max_tok = get_max_request_max_tokens()
    if request.max_tokens is not None and request.max_tokens > max_tok:
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_MAX_TOKENS_TOO_HIGH,
            error_code=ErrorCode.INVALID_REQUEST,
            message=f"max_tokens {request.max_tokens} exceeds server cap {max_tok}.",
            details={"request_max_tokens": request.max_tokens, "server_cap": max_tok},
            http_status=400,
        )

    # ── Rule 4: active request limit (capacity check) ─────────────────
    # The capacity controller is the source of truth for the active
    # limit.  In fixed mode (the default) its effective limit is
    # exactly get_max_active_requests(), so behaviour is unchanged.
    max_active = get_max_active_requests()
    max_queue = get_max_queue_depth()
    controller = _get_controller()

    memory_pressure = _memory_pressure_from_runtime(runtime)
    # The chat-completions route checks runtime readiness *after*
    # admission via ModelResolutionError.  Admission only reasons
    # about capacity; the controller is told the runtime is ready so
    # its QUEUE/REJECT decisions are driven by capacity alone.
    evaluation = controller.evaluate(
        _capacity_context(
            runtime=runtime,
            max_queue_depth=max_queue,
            memory_pressure=memory_pressure,
        )
    )

    # Translate controller verdict back to admission decision.  Keep
    # the public enum stable so existing tests and callers are happy.
    if evaluation.action == CapacityAction.RUN:
        return AdmissionResult(
            accepted=True,
            reason=AdmissionDecision.ACCEPTED,
            http_status=200,
        )

    if evaluation.action == CapacityAction.REJECT:
        # queue_full → REJECTED_QUEUE_FULL.  runtime_not_ready is
        # never reached here (see comment above); if it is, treat as
        # overload for backward compatibility with the pre-throughput
        # contract.
        if evaluation.reason.value == "runtime_not_ready":
            return AdmissionResult(
                accepted=False,
                reason=AdmissionDecision.REJECTED_MODEL_NOT_READY,
                error_code=ErrorCode.MODEL_UNAVAILABLE,
                message="Runtime is not ready.",
                details={"capacity_reason": evaluation.reason.value},
                http_status=503,
            )
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_QUEUE_FULL,
            error_code=ErrorCode.QUEUE_FULL,
            message=(
                f"Whoosh'd is at capacity and the queue is full "
                f"(depth {runtime.queue_depth}/{max_queue})."
            ),
            details={
                "active_jobs": runtime.active_jobs,
                "max_active_requests": max_active,
                "queue_depth": runtime.queue_depth,
                "max_queue_depth": max_queue,
                "capacity_reason": evaluation.reason.value,
                "capacity_mode": evaluation.mode.value,
                "effective_active_limit": evaluation.effective_active_limit,
            },
            http_status=429,
        )

    # QUEUE — queue enabled?  Fall back to REJECTED_OVERLOADED if not.
    if not get_enable_queue():
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_OVERLOADED,
            error_code=ErrorCode.RUNNER_OVERLOADED,
            message=f"Whoosh'd is at its active request limit ({max_active}).",
            details={
                "active_jobs": runtime.active_jobs,
                "max_active_requests": max_active,
                "capacity_reason": evaluation.reason.value,
                "capacity_mode": evaluation.mode.value,
                "effective_active_limit": evaluation.effective_active_limit,
            },
            http_status=429,
        )

    return AdmissionResult(
        accepted=False,
        reason=AdmissionDecision.QUEUED,
        error_code=None,
        message=None,
        details={
            "active_jobs": runtime.active_jobs,
            "max_active_requests": max_active,
            "queue_depth": runtime.queue_depth,
            "max_queue_depth": max_queue,
            "capacity_reason": evaluation.reason.value,
            "capacity_mode": evaluation.mode.value,
            "effective_active_limit": evaluation.effective_active_limit,
        },
        http_status=202,  # Accepted for queueing; caller waits
    )


def _capacity_context(
    *,
    runtime: RuntimeState,
    max_queue_depth: int,
    memory_pressure: MemoryPressureClass,
):
    """Build a :class:`CapacityContext` from a runtime + admission inputs.

    Admission only reasons about capacity, not runtime readiness.  The
    chat-completions route handles readiness separately via
    :class:`ModelResolutionError`.
    """
    from whooshd.capacity_controller import CapacityContext

    return CapacityContext(
        active_jobs=runtime.active_jobs,
        queue_depth=runtime.queue_depth,
        max_queue_depth=max_queue_depth,
        runtime_ready=True,
        memory_pressure=memory_pressure,
    )


def _memory_pressure_from_runtime(runtime: RuntimeState) -> MemoryPressureClass:
    """Translate :class:`RuntimeState.memory` to :class:`MemoryPressureClass`."""
    pressure = getattr(runtime.memory, "pressure", None)
    if pressure is None:
        return MemoryPressureClass.UNKNOWN
    val = getattr(pressure, "value", str(pressure))
    mapping = {
        "normal": MemoryPressureClass.NORMAL,
        "elevated": MemoryPressureClass.ELEVATED,
        "high": MemoryPressureClass.HIGH,
        "critical": MemoryPressureClass.CRITICAL,
    }
    return mapping.get(val.lower(), MemoryPressureClass.UNKNOWN)


# ── Module-level singleton (cached, not re-built per call) ────────────────

_controller: Optional[CapacityController] = None


def _get_controller() -> CapacityController:
    global _controller
    if _controller is None:
        _controller = CapacityController()
    return _controller


def reset_controller_for_tests() -> None:
    """Drop the cached controller.  Tests that change env vars call this."""
    global _controller
    _controller = None


# ── Snapshot helper ────────────────────────────────────────────────────────


def build_capacity_snapshot(
    runtime: RuntimeState,
    *,
    max_queue_depth: int,
) -> dict:
    """Build a safe observability snapshot for the capacity controller.

    Exposes the controller's *verdict* (mode, ceiling, effective limit,
    reason) plus the runtime's *state* (active, queue, depth, runtime
    readiness, memory pressure).  No prompt content, no token IDs, no
    KV handles — safe for dashboards.
    """
    from whooshd.capacity_controller import CapacityController

    controller = _get_controller()
    snapshot = controller.snapshot(
        active_jobs=runtime.active_jobs,
        queue_depth=runtime.queue_depth,
        max_queue_depth=max_queue_depth,
        runtime_ready=runtime.model_lifecycle.value == "ready",
        memory_pressure=_memory_pressure_from_runtime(runtime),
    )
    # Inject the runtime model id when available — useful for dashboards
    # but never a leak (model ids are public).
    model_id = getattr(runtime, "active_model", None) or ""
    snapshot["model"] = model_id
    snapshot["runtime"] = "stub"  # updated by callers that know the adapter
    return snapshot

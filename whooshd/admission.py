"""Admission control — decides whether a request can proceed.

Evaluates configurable limits BEFORE the adapter is invoked.
Rejected requests never become active request lifecycle records.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

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
    max_active = get_max_active_requests()
    if runtime.active_jobs >= max_active:
        # At capacity — check if queueing is enabled.
        queue_enabled = get_enable_queue()
        if queue_enabled:
            max_queue = get_max_queue_depth()
            if runtime.queue_depth < max_queue:
                # Can enqueue.
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
                    },
                    http_status=202,  # Accepted for queueing; caller waits
                )
            else:
                # Queue is full.
                return AdmissionResult(
                    accepted=False,
                    reason=AdmissionDecision.REJECTED_QUEUE_FULL,
                    error_code=ErrorCode.QUEUE_FULL,
                    message=f"Whoosh'd is at capacity and the queue is full (depth {runtime.queue_depth}/{max_queue}).",
                    details={
                        "active_jobs": runtime.active_jobs,
                        "max_active_requests": max_active,
                        "queue_depth": runtime.queue_depth,
                        "max_queue_depth": max_queue,
                    },
                    http_status=429,
                )
        # Queue disabled — reject with overloaded.
        return AdmissionResult(
            accepted=False,
            reason=AdmissionDecision.REJECTED_OVERLOADED,
            error_code=ErrorCode.RUNNER_OVERLOADED,
            message=f"Whoosh'd is at its active request limit ({max_active}).",
            details={"active_jobs": runtime.active_jobs, "max_active_requests": max_active},
            http_status=429,
        )

    return AdmissionResult(accepted=True, reason=AdmissionDecision.ACCEPTED, http_status=200)

"""MLX failure isolation probe — blast-radius check. 🫙💥

Tests whether one failing request can be contained without
poisoning peer requests at the fake-live boundary.  Does NOT
verify shared decode-loop isolation.  Failure remains blocking
until backend-level proof exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class MLXFailureIsolationStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class MLXFailureIsolationFailureReason(str, Enum):
    FAILED_REQUEST_DID_NOT_TERMINATE = "failed_request_did_not_terminate"
    PEER_REQUEST_TERMINATED_UNEXPECTEDLY = "peer_request_terminated_unexpectedly"
    LATE_CHUNK_ACCEPTED_AFTER_FAILURE = "late_chunk_accepted_after_failure"
    FAILURE_SCOPE_MISMATCH = "failure_scope_mismatch"
    FAILURE_REPORT_LEAK = "failure_report_leak"
    IMPORT_FAILED = "import_failed"
    MODEL_LOAD_FAILED = "model_load_failed"
    UNKNOWN = "unknown"


class MLXFailureScope(str, Enum):
    PER_REQUEST = "per_request"
    WHOLE_DECODE_STEP = "whole_decode_step"
    WHOLE_BATCH = "whole_batch"
    BACKEND_FATAL = "backend_fatal"


@dataclass(frozen=True)
class MLXFailureEvent:
    request_id: str
    slot_id: Optional[str] = None
    scope: MLXFailureScope = MLXFailureScope.PER_REQUEST
    sanitized_error_kind: str = "unknown"
    affected_request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MLXFailureIsolationReport:
    backend: str = "mlx"
    probe: str = "failure_isolation"
    status: MLXFailureIsolationStatus = MLXFailureIsolationStatus.NOT_RUN
    failure_reason: Optional[MLXFailureIsolationFailureReason] = None
    failed_request_id: Optional[str] = None
    peer_request_ids: tuple[str, ...] = ()
    failure_scope: Optional[MLXFailureScope] = None
    failed_request_terminal: bool = False
    peers_continued: bool = False
    late_chunks_rejected: bool = False
    failure_report_sanitized: bool = True
    failure_isolation_backend_verified: bool = False
    shared_decode_loop_verified: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False


# ── Classification ─────────────────────────────────────────────────────────


def classify_mlx_failure_scope(
    *,
    failed_request_id: str,
    active_request_ids: Sequence[str],
    affected_request_ids: Sequence[str],
    backend_fatal: bool = False,
) -> MLXFailureScope:
    if backend_fatal:
        return MLXFailureScope.BACKEND_FATAL
    affected = set(affected_request_ids)
    active = set(active_request_ids)
    if affected == active:
        return MLXFailureScope.WHOLE_BATCH
    if affected == {failed_request_id}:
        return MLXFailureScope.PER_REQUEST
    if failed_request_id in affected and len(affected) > 1:
        return MLXFailureScope.WHOLE_DECODE_STEP
    return MLXFailureScope.PER_REQUEST


# ── Event building ─────────────────────────────────────────────────────────


def build_mlx_failure_event(
    *,
    request_id: str,
    slot_id: Optional[str] = None,
    error: Optional[BaseException] = None,
    scope: MLXFailureScope = MLXFailureScope.PER_REQUEST,
    affected_request_ids: Sequence[str] = (),
) -> MLXFailureEvent:
    sanitized = type(error).__name__ if error is not None else "unknown"
    return MLXFailureEvent(
        request_id=request_id,
        slot_id=slot_id,
        scope=scope,
        sanitized_error_kind=sanitized,
        affected_request_ids=tuple(affected_request_ids),
    )


# ── Report building ────────────────────────────────────────────────────────


def build_mlx_failure_isolation_report(
    *,
    failure_event: MLXFailureEvent,
    peer_request_ids: Sequence[str],
    failed_request_terminal: bool,
    peers_continued: bool,
    late_chunks_rejected: bool,
    generated_text_included: bool = False,
) -> MLXFailureIsolationReport:
    passed = failed_request_terminal and peers_continued and late_chunks_rejected
    status = MLXFailureIsolationStatus.PASSED if passed else MLXFailureIsolationStatus.FAILED
    reason = None
    if not failed_request_terminal:
        reason = MLXFailureIsolationFailureReason.FAILED_REQUEST_DID_NOT_TERMINATE
    elif not peers_continued:
        reason = MLXFailureIsolationFailureReason.PEER_REQUEST_TERMINATED_UNEXPECTEDLY
    elif not late_chunks_rejected:
        reason = MLXFailureIsolationFailureReason.LATE_CHUNK_ACCEPTED_AFTER_FAILURE
    return MLXFailureIsolationReport(
        status=status,
        failure_reason=reason,
        failed_request_id=failure_event.request_id,
        peer_request_ids=tuple(peer_request_ids),
        failure_scope=failure_event.scope,
        failed_request_terminal=failed_request_terminal,
        peers_continued=peers_continued,
        late_chunks_rejected=late_chunks_rejected,
        generated_text_included=generated_text_included,
    )

"""Versioned Codexify <-> Whoosh'd control-plane contract.

This module is deliberately independent from the request/response models so
that error vocabulary, categories, retry semantics, and HTTP mapping have one
authoritative owner.  Values in an error envelope are operational metadata;
callers must not pass exception text or request bodies as messages/details.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from whooshd.correlation import normalize_identifier


CONTROL_PLANE_CONTRACT_VERSION = "whooshd.control.v1"
CONTROL_PLANE_VERSION_HEADER = "X-Whooshd-Contract-Version"
DEFAULT_RETRY_AFTER_SECONDS = 2.0
_SAFE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,96}$")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/ ()'=-]{1,160}$")
_SAFE_DIAGNOSTIC_RE = re.compile(
    r"^exception_type=[A-Za-z0-9_.-]{1,80} failure_class=[a-z_]{1,40}$"
)
_SAFE_VERSION_RE = re.compile(r"^whooshd\.control\.v[0-9]+$")


class ErrorCode(str, Enum):
    """Stable machine-readable v1 failure codes."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_FIELD = "unsupported_field"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CONTRACT_VERSION_UNSUPPORTED = "contract_version_unsupported"

    MODEL_NOT_FOUND = "model_not_found"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_WARMING = "model_warming"
    MODEL_LOAD_FAILED = "model_load_failed"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_DEGRADED = "runtime_degraded"

    RUNNER_OVERLOADED = "runner_overloaded"
    QUEUE_FULL = "queue_full"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CONTEXT_OVERFLOW = "context_overflow"

    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_PROTOCOL_ERROR = "upstream_protocol_error"
    STREAM_INTERRUPTED = "stream_interrupted"
    MALFORMED_UPSTREAM_RESPONSE = "malformed_upstream_response"

    INTERNAL_ERROR = "internal_error"

    # Compatibility names retained for callers that imported the pre-v1 enum.
    INTERNAL = "internal_error"
    MEMORY_PRESSURE = "runtime_degraded"


class ErrorCategory(str, Enum):
    REQUEST_CONTRACT = "request_contract"
    MODEL_RUNTIME = "model_runtime"
    CAPACITY_EXECUTION = "capacity_execution"
    UPSTREAM_PROTOCOL = "upstream_protocol"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorSpec:
    category: ErrorCategory
    http_status: int
    retryable: bool
    retry_after_seconds: float | None = None


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.INVALID_REQUEST: ErrorSpec(ErrorCategory.REQUEST_CONTRACT, 400, False),
    ErrorCode.UNSUPPORTED_FIELD: ErrorSpec(ErrorCategory.REQUEST_CONTRACT, 400, False),
    ErrorCode.UNSUPPORTED_CAPABILITY: ErrorSpec(ErrorCategory.REQUEST_CONTRACT, 422, False),
    ErrorCode.CONTRACT_VERSION_UNSUPPORTED: ErrorSpec(
        ErrorCategory.REQUEST_CONTRACT, 400, False
    ),
    ErrorCode.MODEL_NOT_FOUND: ErrorSpec(ErrorCategory.MODEL_RUNTIME, 404, False),
    ErrorCode.MODEL_UNAVAILABLE: ErrorSpec(ErrorCategory.MODEL_RUNTIME, 503, True),
    ErrorCode.MODEL_WARMING: ErrorSpec(
        ErrorCategory.MODEL_RUNTIME, 425, True, DEFAULT_RETRY_AFTER_SECONDS
    ),
    ErrorCode.MODEL_LOAD_FAILED: ErrorSpec(ErrorCategory.MODEL_RUNTIME, 500, False),
    ErrorCode.RUNTIME_UNAVAILABLE: ErrorSpec(ErrorCategory.MODEL_RUNTIME, 503, True),
    ErrorCode.RUNTIME_DEGRADED: ErrorSpec(ErrorCategory.MODEL_RUNTIME, 503, True),
    ErrorCode.RUNNER_OVERLOADED: ErrorSpec(
        ErrorCategory.CAPACITY_EXECUTION, 429, True, DEFAULT_RETRY_AFTER_SECONDS
    ),
    ErrorCode.QUEUE_FULL: ErrorSpec(
        ErrorCategory.CAPACITY_EXECUTION, 429, True, DEFAULT_RETRY_AFTER_SECONDS
    ),
    ErrorCode.TIMEOUT: ErrorSpec(ErrorCategory.CAPACITY_EXECUTION, 504, True),
    ErrorCode.CANCELLED: ErrorSpec(ErrorCategory.CAPACITY_EXECUTION, 409, False),
    ErrorCode.CONTEXT_OVERFLOW: ErrorSpec(
        ErrorCategory.CAPACITY_EXECUTION, 422, False
    ),
    ErrorCode.UPSTREAM_UNAVAILABLE: ErrorSpec(
        ErrorCategory.UPSTREAM_PROTOCOL, 503, True
    ),
    ErrorCode.UPSTREAM_TIMEOUT: ErrorSpec(ErrorCategory.UPSTREAM_PROTOCOL, 504, True),
    ErrorCode.UPSTREAM_PROTOCOL_ERROR: ErrorSpec(
        ErrorCategory.UPSTREAM_PROTOCOL, 502, False
    ),
    ErrorCode.STREAM_INTERRUPTED: ErrorSpec(
        ErrorCategory.UPSTREAM_PROTOCOL, 502, False
    ),
    ErrorCode.MALFORMED_UPSTREAM_RESPONSE: ErrorSpec(
        ErrorCategory.UPSTREAM_PROTOCOL, 502, False
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(ErrorCategory.INTERNAL, 500, False),
}


_SAFE_DETAIL_KEYS = {
    "active_jobs",
    "adapter",
    "adapter_kind",
    "body_bytes",
    "body_present",
    "content_type",
    "contract_version",
    "diagnostic",
    "endpoint",
    "endpoint_kind",
    "error_count",
    "error_code",
    "estimated_chars",
    "failure_class",
    "frame_bytes",
    "http_status",
    "max_active_requests",
    "max_messages",
    "max_prompt_chars",
    "max_queue_depth",
    "model_alias",
    "model_id",
    "output_started",
    "parser_failure_class",
    "policy_version",
    "queue_depth",
    "rejected_field_count",
    "request_id",
    "received_version",
    "runtime_kind",
    "status",
    "timeout_seconds",
    "transport_class",
    "upstream_status",
}
_SAFE_STRING_KEYS = {
    "adapter",
    "adapter_kind",
    "content_type",
    "contract_version",
    "endpoint",
    "endpoint_kind",
    "error_code",
    "failure_class",
    "model_alias",
    "model_id",
    "parser_failure_class",
    "policy_version",
    "request_id",
    "received_version",
    "runtime_kind",
    "status",
    "transport_class",
}


def bounded_details(details: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only known, scalar operational detail fields.

    This is a second line of defence for handlers: values that are not
    explicitly operational metadata are omitted rather than echoed.
    """

    if not isinstance(details, Mapping):
        return None
    bounded: dict[str, Any] = {}
    for raw_key, value in details.items():
        key = str(raw_key)
        if key not in _SAFE_DETAIL_KEYS or not _SAFE_NAME_RE.fullmatch(key):
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            bounded[key] = value
        elif key == "diagnostic" and isinstance(value, str):
            if _SAFE_DIAGNOSTIC_RE.fullmatch(value[:180]):
                bounded[key] = value[:180]
        elif key in _SAFE_STRING_KEYS and isinstance(value, str):
            if _SAFE_VALUE_RE.fullmatch(value[:160]):
                bounded[key] = value[:160]
        elif key in {"body_present", "output_started"}:
            bounded[key] = bool(value)
    return bounded or None


def safe_contract_version(value: Any) -> str:
    """Return only a bounded, recognizable incoming version identifier."""

    candidate = str(value or "").strip()
    if len(candidate) > 80 or not _SAFE_VERSION_RE.fullmatch(candidate):
        return "invalid"
    return candidate


def error_spec(code: ErrorCode | str) -> ErrorSpec:
    """Return the authoritative v1 semantics for *code*."""

    normalized = code if isinstance(code, ErrorCode) else ErrorCode(code)
    return ERROR_SPECS[normalized]


def error_fields(
    code: ErrorCode | str,
    *,
    message: str,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
    request_id: str | None = None,
    upstream_request_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical, bounded error envelope fields."""

    normalized = code if isinstance(code, ErrorCode) else ErrorCode(code)
    spec = error_spec(normalized)
    retry_after = (
        spec.retry_after_seconds
        if retry_after_seconds is None
        else max(0.0, min(float(retry_after_seconds), 60.0))
    )
    return {
        "contract_version": CONTROL_PLANE_CONTRACT_VERSION,
        "code": normalized,
        "message": str(message or "Request failed")[:160],
        "http_status": int(http_status if http_status is not None else spec.http_status),
        "retryable": spec.retryable,
        "retry_after_seconds": retry_after,
        # request_id remains the established Whoosh'd lifecycle identifier.
        # The upstream caller identity is carried separately and never replaces
        # cancellation, queue, batch, or adapter state.
        "request_id": normalize_identifier(request_id),
        "upstream_request_id": normalize_identifier(upstream_request_id),
        "category": spec.category,
        "details": bounded_details(details),
    }


def code_for_http_status(status: int) -> ErrorCode:
    """Fallback classification for an otherwise untyped HTTP failure."""

    if status == 404:
        return ErrorCode.MODEL_NOT_FOUND
    if status in (408, 504):
        return ErrorCode.UPSTREAM_TIMEOUT
    if status == 425:
        return ErrorCode.MODEL_WARMING
    if status == 429:
        return ErrorCode.RUNNER_OVERLOADED
    if status == 503:
        return ErrorCode.RUNTIME_UNAVAILABLE
    if status == 422:
        return ErrorCode.INVALID_REQUEST
    if 400 <= status < 500:
        return ErrorCode.INVALID_REQUEST
    if status == 502:
        return ErrorCode.UPSTREAM_PROTOCOL_ERROR
    return ErrorCode.INTERNAL_ERROR

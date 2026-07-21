"""Process-wide content and credential safe logging for Whoosh'd.

The boundary is installed before application modules create their loggers.
It protects normal logging handlers, pytest handlers, and framework-created
records without changing request or response payload contracts.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"
_SANITIZED = "_whooshd_log_sanitized"

_SAFE_FIELDS = {
    "active_jobs",
    "adapter",
    "adapter_type",
    "adapter_kind",
    "actual_request_id",
    "backend",
    "binary_path_present",
    "body_bytes",
    "body_present",
    "cache_key",
    "cache_scope",
    "chain_hash",
    "bytes",
    "content_type",
    "duration_ms",
    "dynamic_tokens",
    "eligible",
    "endpoint_kind",
    "expected_request_id",
    "exception_type",
    "error_code",
    "event",
    "failure_class",
    "failure_kind",
    "frame_bytes",
    "frame_count",
    "host",
    "http_status",
    "item_count",
    "kind",
    "lifecycle",
    "mode",
    "model",
    "model_alias",
    "model_id",
    "model_path_present",
    "output_begun",
    "output_started",
    "prev_segments",
    "new_segments",
    "parser_failure_class",
    "pid",
    "port",
    "queue_depth",
    "request_id",
    "request_count",
    "runtime",
    "runtime_kind",
    "reason",
    "scope",
    "status",
    "status_code",
    "timeout_class",
    "timeout_seconds",
    "token_count",
    "transport",
    "transport_class",
    "stable_prefix_hash",
    "stable_prefix_tokens",
    "estimated_prefill_reuse_tokens",
    "appended_tokens",
    "thread",
    "thread_id",
    "task_id",
    "turn_id",
    "diagnostic",
    "type",
    "name",
}

_UNSAFE_FIELD_RE = re.compile(
    r"(?i)(?:prompt|message|content|completion|response|request|body|payload|"
    r"output|generated|assistant|text|media|image|base64|stream|frame|line|"
    r"token|cookie|authorization|bearer|api[_-]?key|secret|"
    r"password|header|tool|argument|args|result|stderr|stdout|exception|"
    r"error|detail|path|cwd|argv|command|url|query)"
)
_PLACEHOLDER_RE = re.compile(
    r"%(?:\(([^)]+)\))?[#0\- +]?\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]"
)
_FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.-]*)\s*(?:=|:)\s*$")
_AUTH_RE = re.compile(
    r"(?i)(\b(?:authorization\s*:\s*(?:bearer|basic)|bearer)\s+)[^\s,;]+"
)
_COOKIE_RE = re.compile(r"(?i)(\bcookie\s*:\s*)[^\r\n]+")
_QUERY_RE = re.compile(
    r"([?&](?:api[_-]?key|token|secret|code|auth|signature|session)=[^\s&#]+)",
    re.I,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?secret|"
    r"password|client[_-]?secret)\s*[=:]\s*)[^\s,;]+"
)
_SAFE_DIAGNOSTIC_RE = re.compile(
    r"^exception_type=[A-Za-z0-9_.-]{1,80} failure_class=[a-z_]{1,40}$"
)
_STANDARD_FIELDS = set(
    logging.LogRecord("_", logging.INFO, __file__, 0, "", (), None).__dict__
)

_factory_installed = False
_make_record_installed = False
_original_factory = logging.getLogRecordFactory()
_original_make_record = logging.Logger.makeRecord


def failure_class(exception: BaseException | None) -> str:
    if exception is None:
        return "unknown"
    name = type(exception).__name__.lower()
    text = str(exception).lower()
    if "timeout" in name or "timeout" in text:
        return "timeout"
    if "connect" in name or "connection" in text:
        return "connection"
    if "json" in name or "parse" in name or "decode" in text:
        return "parse"
    if "permission" in name or "auth" in name:
        return "authorization"
    if "validation" in name or "value" in name:
        return "validation"
    if "http" in name or "status" in name:
        return "http"
    return "runtime"


def exception_metadata(exception: BaseException | None) -> str:
    if exception is None:
        return "exception_type=unknown failure_class=unknown"
    return (
        f"exception_type={type(exception).__name__[:80]} "
        f"failure_class={failure_class(exception)}"
    )


def safe_exception_message(
    exception: BaseException | None, *, default: str = "runtime failure"
) -> str:
    """Return a type-only diagnostic suitable for outward error messages.

    Exception text is untrusted: even a transport or parser exception can
    contain a URL, response body, prompt, local path, or subprocess stderr.
    The exception type and failure class retain bounded diagnostic value.
    """
    if exception is None:
        return default
    return f"{type(exception).__name__} failure ({failure_class(exception)})"


def safe_model_alias(value: Any) -> str:
    """Keep a short model alias while excluding private paths and controls."""
    text = str(value or "")
    if (
        not text
        or len(text) > 128
        or text.startswith(("/", "~/", "./", "../"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        or "://" in text
        or "@" in text
        or "?" in text
        or text.endswith((".gguf", ".safetensors", ".bin"))
        or text.startswith(("models/", "private/", "secret/"))
        or any(ord(char) < 32 for char in text)
    ):
        return REDACTED
    return text


def safe_url(value: str) -> str:
    """Retain endpoint identity while dropping userinfo, query, and fragment."""
    try:
        parsed = urlsplit(str(value))
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return REDACTED
        port = f":{parsed.port}" if parsed.port is not None else ""
        path = parsed.path or ""
        if len(path) > 128 or not re.fullmatch(r"[A-Za-z0-9/._~-]*", path):
            path = ""
        return urlunsplit((parsed.scheme, f"{host}{port}", path, "", ""))[:256]
    except (TypeError, ValueError):
        return REDACTED


def _looks_like_private_path(value: str) -> bool:
    return (
        value.startswith(("/", "~/", "./", "../"))
        or os.sep in value
        or (os.altsep and os.altsep in value)
        or bool(re.match(r"^[A-Za-z]:[\\/]", value))
    )


def _summary(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{REDACTED} bytes={len(value)}"
    if isinstance(value, str):
        return f"{REDACTED} chars={len(value)}"
    if isinstance(value, Mapping):
        return f"{REDACTED} items={len(value)}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return f"{REDACTED} items={len(value)}"
    return REDACTED


def _placeholder_fields(template: str) -> list[str | None]:
    fields: list[str | None] = []
    previous_end = 0
    for match in _PLACEHOLDER_RE.finditer(template):
        prefix = template[previous_end : match.start()]
        field_match = _FIELD_RE.search(prefix)
        fields.append(field_match.group(1).lower() if field_match else None)
        previous_end = match.end()
    return fields


def _sanitize_value(value: Any, field: str | None = None) -> Any:
    normalized = field.lower() if field else None
    if isinstance(value, BaseException):
        return exception_metadata(value)
    if normalized in {"url", "endpoint", "server_url", "base_url"}:
        return safe_url(str(value))
    if normalized in _SAFE_FIELDS:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if normalized == "diagnostic" and not _SAFE_DIAGNOSTIC_RE.fullmatch(value):
                return _summary(value)
            if normalized in {"model", "model_id", "model_alias"}:
                return safe_model_alias(value)
            if _looks_like_private_path(value):
                return f"{REDACTED} path_present=True"
            return value.replace("\n", " ").replace("\r", " ")[:160]
        if isinstance(value, Mapping):
            return {"items": len(value)}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return {"items": len(value)}
        return str(value)[:80]
    if normalized and _UNSAFE_FIELD_RE.search(normalized):
        return _summary(value)
    if isinstance(value, (str, bytes, bytearray, memoryview, Mapping, Sequence)):
        return _summary(value)
    return value


def sanitize_log_text(text: str) -> str:
    output = _AUTH_RE.sub(r"\1<redacted>", text)
    output = _COOKIE_RE.sub(r"\1<redacted>", output)
    output = _QUERY_RE.sub("<redacted-query>", output)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1<redacted>", output)


def _sanitize_freeform(message: Any) -> str:
    if not isinstance(message, str):
        return _summary(message)
    scrubbed = sanitize_log_text(message)
    lowered = scrubbed.lower()
    if len(scrubbed) > 200 or any(
        marker in lowered
        for marker in (
            "prompt",
            "message",
            "content",
            "completion",
            "response",
            "payload",
            "output",
            "generated",
            "assistant",
            "media",
            "tool",
            "image",
            "base64",
            "stderr",
            "stdout",
            "raw",
        )
    ):
        return f"log_event={REDACTED} chars={len(message)}"
    if re.fullmatch(r"[A-Za-z0-9_.:-]+", scrubbed) or re.fullmatch(
        r"\[[A-Za-z0-9_.:-]+\]\s+(?:started|stopped|failed|ready|"
        r"available|disabled|enabled|cancelled)",
        scrubbed,
        re.I,
    ):
        return scrubbed
    return f"log_event={REDACTED} chars={len(message)}"


def sanitize_record(
    record: logging.LogRecord, *, force: bool = False
) -> logging.LogRecord:
    if getattr(record, _SANITIZED, False) and not force:
        return record
    if isinstance(record.args, Mapping):
        record.args = {
            key: _sanitize_value(value, str(key))
            for key, value in record.args.items()
        }
    elif record.args:
        template = str(record.msg)
        fields = _placeholder_fields(template)
        record.args = tuple(
            _sanitize_value(value, fields[index] if index < len(fields) else None)
            for index, value in enumerate(record.args)
        )
    else:
        record.msg = _sanitize_freeform(record.msg)
    if record.exc_info:
        exception = record.exc_info[1] if len(record.exc_info) > 1 else None
        record.msg = f"{record.msg} {exception_metadata(exception)}"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
    for key, value in list(record.__dict__.items()):
        if key in _STANDARD_FIELDS or key.startswith("_"):
            continue
        setattr(record, key, _sanitize_value(value, key))
    setattr(record, _SANITIZED, True)
    return record


class SafeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        sanitize_record(record, force=True)
        return True


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    return sanitize_record(_original_factory(*args, **kwargs))


def _make_record(
    logger: logging.Logger, *args: Any, **kwargs: Any
) -> logging.LogRecord:
    return sanitize_record(
        _original_make_record(logger, *args, **kwargs), force=True
    )


def install_safe_logging() -> None:
    """Install the record and handler boundary exactly once."""
    global _factory_installed, _make_record_installed
    if not _factory_installed:
        logging.setLogRecordFactory(_record_factory)
        _factory_installed = True
    if not _make_record_installed:
        logging.Logger.makeRecord = _make_record
        _make_record_installed = True
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, SafeLogFilter) for item in handler.filters):
            handler.addFilter(SafeLogFilter())

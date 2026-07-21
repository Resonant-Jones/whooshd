"""HTTP forwarding utilities for runtime backend adapters.

Provides shared async helpers for:
  * JSON POST forwarding (non-streaming)
  * Server-Sent Event (SSE) streaming forwarding
  * Safe header forwarding
  * Upstream error classification
  * Client disconnect handling

Used by llama.cpp and MLX-LM Server adapters to proxy requests
to their respective HTTP servers.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionDelta,
    ChatCompletionResponse,
)
from whooshd.backend_request_policy import ensure_backend_chat_request
from whooshd.control_plane import ErrorCode
from whooshd.log_safety import failure_class, safe_model_alias, safe_url

logger = logging.getLogger(__name__)

# Optional httpx import — only when forwarding is active.
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

# ── Safe header set ─────────────────────────────────────────────────────────

_SAFE_REQUEST_HEADERS = {
    "authorization",
    "content-type",
    "accept",
    "accept-encoding",
}

# Hop-by-hop headers that must never be forwarded.
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _filter_safe_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Return only safe-to-forward request headers."""
    if headers is None:
        return {}
    return {
        k: v
        for k, v in headers.items()
        if k.lower() in _SAFE_REQUEST_HEADERS
        and k.lower() not in _HOP_BY_HOP_HEADERS
    }


# ── Request body builder ────────────────────────────────────────────────────


def build_forward_body(
    request,
    *,
    model_override: str | None = None,
    adapter_kind: str | None = None,
) -> dict:
    """Build a JSON-serialisable dict from a ChatCompletionRequest.

    Preserves allowlisted canonical fields and declared adapter extensions.
    Internal metadata and unknown fields never reach the body. Fields with
    ``None`` values are omitted from the forward body.

    *model_override* allows the adapter to set a different model ID
    for the upstream server.
    """
    request = ensure_backend_chat_request(request, adapter_kind=adapter_kind)
    allowed_fields = set(request.forwarded_fields)
    body: dict = {
        "model": model_override if model_override else request.model,
        "messages": [
            _serialize_message(m) for m in request.messages
        ],
        "stream": request.stream,
    }

    # ── Fields to forward when non-None ───────────────────────────
    _maybe_set(body, "temperature", request.temperature, allowed_fields)
    _maybe_set(body, "top_p", request.top_p, allowed_fields)
    _maybe_set(body, "max_tokens", request.max_tokens, allowed_fields)
    _maybe_set(body, "max_completion_tokens", request.max_completion_tokens, allowed_fields)
    _maybe_set(body, "stop", request.stop, allowed_fields)
    _maybe_set(body, "user", request.user, allowed_fields)

    # Tool / function calling.
    _maybe_set(body, "tools", request.tools, allowed_fields)
    _maybe_set(body, "tool_choice", request.tool_choice, allowed_fields)
    _maybe_set(body, "parallel_tool_calls", request.parallel_tool_calls, allowed_fields)

    # Structured output.
    _maybe_set(body, "response_format", request.response_format, allowed_fields)

    # Sampling parameters.
    _maybe_set(body, "seed", request.seed, allowed_fields)
    _maybe_set(body, "presence_penalty", request.presence_penalty, allowed_fields)
    _maybe_set(body, "frequency_penalty", request.frequency_penalty, allowed_fields)
    _maybe_set(body, "logit_bias", request.logit_bias, allowed_fields)
    _maybe_set(body, "logprobs", request.logprobs, allowed_fields)
    _maybe_set(body, "top_logprobs", request.top_logprobs, allowed_fields)

    # Reasoning.
    _maybe_set(body, "reasoning_effort", request.reasoning_effort, allowed_fields)

    # Only extensions explicitly declared for the selected adapter survive
    # the backend request policy.
    extra = getattr(request, "extra_fields", None) or {}
    for key, value in extra.items():
        if value is not None:
            body[key] = value

    return body


def _maybe_set(d: dict, key: str, value, allowed_fields: set[str] | None = None) -> None:
    """Set *key* in *d* to *value* if value is not None."""
    if value is not None and (allowed_fields is None or key in allowed_fields):
        d[key] = value


def _serialize_message(m) -> dict:
    """Serialize a ChatMessage to a dict, preserving all known fields
    including tool_calls and tool_call_id.
    """
    d: dict = {"role": m.role, "content": m.content}
    if m.name:
        d["name"] = m.name
    if m.tool_calls is not None:
        d["tool_calls"] = m.tool_calls
    if m.tool_call_id is not None:
        d["tool_call_id"] = m.tool_call_id
    return d


# ── Non-streaming forwarding ────────────────────────────────────────────────


async def forward_non_streaming(
    server_url: str,
    request,
    *,
    endpoint: str = "/v1/chat/completions",
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
    model_override: str | None = None,
    adapter_kind: str | None = None,
) -> ChatCompletionResponse:
    """Forward a non-streaming chat completion request to an upstream server.

    Args:
        server_url: Base URL of the upstream server (e.g. ``http://127.0.0.1:8080``).
        request: A ``ChatCompletionRequest`` instance.
        endpoint: Upstream endpoint path (default ``/v1/chat/completions``).
        timeout: Total request timeout in seconds.
        headers: Optional extra headers to forward.
        model_override: Override the ``model`` field in the upstream body.

    Returns:
        A ``ChatCompletionResponse`` parsed from the upstream JSON.

    Raises:
        UpstreamConnectionError: If the server is unreachable.
        UpstreamTimeoutError: If the request times out.
        UpstreamHTTPError: If the server returns a non-2xx status.
    """
    _check_httpx()
    url = f"{server_url.rstrip('/')}{endpoint}"
    body = build_forward_body(
        request,
        model_override=model_override,
        adapter_kind=adapter_kind,
    )
    safe_req_headers = _filter_safe_headers(headers)
    safe_req_headers["Content-Type"] = "application/json"

    logger.info(
        "forward.non_streaming endpoint_kind=%s model=%s request_id=%s",
        _endpoint_kind(endpoint),
        body.get("model"),
        getattr(request, "request_id", None),
    )

    resp = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body, headers=safe_req_headers)
    except Exception as exc:
        raise _classify_request_exception(exc, server_url, timeout) from exc

    # If we got here, resp is set.  Classify by status code.
    if resp.status_code == 200:
        try:
            data = resp.json()
            return ChatCompletionResponse.model_validate(data)
        except Exception as exc:
            raise UpstreamHTTPError(
                "Upstream returned an unparsable successful response",
                status_code=502,
                detail={
                    "failure_class": failure_class(exc),
                    "http_status": 200,
                    "body_bytes": _byte_count(resp.content),
                    "body_present": bool(resp.content),
                    "content_type": str(resp.headers.get("content-type", ""))[:80],
                },
                error_code=ErrorCode.MALFORMED_UPSTREAM_RESPONSE,
            ) from exc

    if resp.status_code == 400:
        _detail = _safe_upstream_body(resp)
        raise UpstreamBadRequest(
            f"Upstream rejected request: {resp.status_code}",
            detail=_detail,
        )

    if resp.status_code == 404:
        raise RuntimeModelNotFound("Model not found on upstream server")

    if resp.status_code in (408, 504):
        raise UpstreamTimeoutError(
            f"Upstream server reported timeout: {resp.status_code}",
            detail=_safe_upstream_body(resp),
        )

    if resp.status_code == 429:
        raise RuntimeWarming(
            "Upstream is not ready; model may be loading.",
            detail=_safe_upstream_body(resp),
        )

    # Generic upstream error.
    _detail = _safe_upstream_body(resp)
    raise UpstreamHTTPError(
        f"Upstream returned {resp.status_code}",
        status_code=502,
        detail=_detail,
    )


# ── Streaming forwarding ────────────────────────────────────────────────────


async def forward_streaming(
    server_url: str,
    request,
    *,
    endpoint: str = "/v1/chat/completions",
    timeout: float = 300.0,
    headers: dict[str, str] | None = None,
    model_override: str | None = None,
    adapter_kind: str | None = None,
    cancellation_token=None,
) -> AsyncIterator[ChatCompletionChunk]:
    """Forward a streaming chat completion request to an upstream server.

    Reads the upstream SSE stream chunk-by-chunk, parsing each OpenAI-compatible
    SSE line into a ``ChatCompletionChunk``.

    Supports:
      * Pure SSE passthrough — chunks are already OpenAI-compatible.
      * Cancellation — checks *cancellation_token* between chunks.
      * Client disconnect — detected via ``GeneratorExit``.

    Args:
        server_url: Base URL of the upstream server.
        request: A ``ChatCompletionRequest`` instance.
        endpoint: Upstream endpoint path.
        timeout: Total stream timeout in seconds.
        headers: Optional extra headers to forward.
        model_override: Override the ``model`` field.
        cancellation_token: Optional ``CancellationToken`` for cooperative cancel.

    Yields:
        ``ChatCompletionChunk`` instances suitable for SSE serialization.

    Raises:
        UpstreamConnectionError: If the server is unreachable.
        UpstreamTimeoutError: If the request times out.
        UpstreamHTTPError: If the server returns an error status.
        StreamInterrupted: If the upstream stream is broken mid-response.
    """
    _check_httpx()
    url = f"{server_url.rstrip('/')}{endpoint}"
    body = build_forward_body(
        request,
        model_override=model_override,
        adapter_kind=adapter_kind,
    )
    body["stream"] = True
    safe_req_headers = _filter_safe_headers(headers)
    safe_req_headers["Content-Type"] = "application/json"
    safe_req_headers["Accept"] = "text/event-stream"

    logger.info(
        "forward.streaming endpoint_kind=%s model=%s request_id=%s",
        _endpoint_kind(endpoint),
        body.get("model"),
        getattr(request, "request_id", None),
    )

    response = None
    output_started = False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, json=body, headers=safe_req_headers
            ) as response:
                if response.status_code != 200:
                    # Read error body safely.
                    try:
                        err_body = await response.aread()
                    except Exception:
                        err_body = b""
                    safe_body = _safe_upstream_body(
                        response,
                        body_bytes=_byte_count(err_body),
                    )
                    if response.status_code == 400:
                        raise UpstreamBadRequest(
                            "Upstream rejected the streaming request",
                            detail=safe_body,
                        )
                    if response.status_code == 404:
                        raise RuntimeModelNotFound(
                            "Model was not found on the upstream server"
                        )
                    if response.status_code in (408, 504):
                        raise UpstreamTimeoutError(
                            "Upstream streaming request timed out",
                            detail=safe_body,
                        )
                    if response.status_code == 429:
                        raise RuntimeWarming(
                            "Upstream model is warming",
                            detail=safe_body,
                        )
                    raise UpstreamHTTPError(
                        "Upstream returned an error for the streaming request",
                        status_code=502,
                        detail=safe_body,
                    )

                async for line in response.aiter_lines():
                    # Check cancellation.
                    if cancellation_token and cancellation_token.is_cancelled():
                        logger.info("forward.streaming.cancelled")
                        return

                    # SSE lines start with "data: ".
                    if not line.startswith("data: "):
                        continue

                    data_str = line[len("data: "):]

                    # Skip [DONE] sentinel — the caller adds its own.
                    if data_str.strip() == "[DONE]":
                        continue

                    try:
                        chunk_data = json.loads(data_str)
                        chunk = ChatCompletionChunk.model_validate(chunk_data)
                        output_started = True
                        yield chunk
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning(
                            "forward.streaming.parse_error "
                            "parser_failure_class=%s frame_bytes=%s "
                            "output_started=%s",
                            type(exc).__name__,
                            len(line.encode("utf-8", errors="replace")),
                            output_started,
                        )
                        # Skip unparseable chunks rather than failing the stream.
                        continue

    except GeneratorExit:
        # Client disconnected — let the upstream stream close naturally
        # by exiting the generator.
        logger.info("forward.streaming.client_disconnect")
        raise
    except UpstreamRuntimeError:
        raise
    except Exception as exc:
        raise _classify_request_exception(exc, server_url, timeout) from exc
    finally:
        # Best-effort close of the upstream response.
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                pass


# ── Internal helpers ────────────────────────────────────────────────────────


def _check_httpx():
    """Raise ImportError if httpx is not installed."""
    if httpx is None:
        raise ImportError(
            "httpx is required for HTTP forwarding. "
            "Install it with: pip install httpx"
        )


def _endpoint_kind(endpoint: str) -> str:
    """Map an endpoint path to a bounded operational label."""
    path = str(endpoint).rstrip("/").lower()
    if path.endswith("/chat/completions"):
        return "chat_completions"
    if path.endswith("/generate"):
        return "generate"
    return "other"


def _byte_count(value) -> int:
    """Return a body size without retaining or decoding the body."""
    if isinstance(value, (bytes, bytearray, memoryview, str)):
        return len(value if not isinstance(value, str) else value.encode())
    return 0


def _safe_upstream_body(resp, *, body_bytes: int | None = None) -> dict:
    """Return bounded upstream response metadata without retaining its body."""
    try:
        if body_bytes is None:
            content = getattr(resp, "content", None)
            body_bytes = _byte_count(content)
            if body_bytes == 0:
                body_bytes = _byte_count(getattr(resp, "text", ""))
        headers = getattr(resp, "headers", {}) or {}
        content_type = headers.get("content-type")
        return {
            "upstream_status": resp.status_code,
            "http_status": resp.status_code,
            "body_bytes": body_bytes,
            "body_present": bool(body_bytes),
            "content_type": str(content_type).split(";", 1)[0][:80]
            if content_type
            else None,
        }
    except Exception:
        return {"upstream_status": getattr(resp, "status_code", None)}


def _classify_request_exception(exc: Exception, server_url: str, timeout: float) -> UpstreamRuntimeError:
    """Classify a transport-level exception into a typed UpstreamRuntimeError.

    Uses string-based classification rather than typed except clauses
    so that tests can safely mock httpx without type errors.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()
    metadata = {
        "failure_class": failure_class(exc),
        "endpoint": safe_url(server_url),
        "timeout_seconds": timeout,
    }

    if "connect" in exc_name.lower() or "connection" in exc_msg:
        return UpstreamConnectionError(
            "Upstream server is not reachable.", detail=metadata
        )

    if "readtimeout" in exc_name.lower() or "timeout" in exc_name.lower() or "timeout" in exc_msg:
        return UpstreamTimeoutError("Upstream server timed out.", detail=metadata)

    # Generic fallback — unexpected transport error.
    return UpstreamHTTPError(
        "Unexpected upstream transport failure",
        status_code=502,
        detail=metadata,
    )


# ── Error classes ───────────────────────────────────────────────────────────


class UpstreamRuntimeError(Exception):
    """Base class for upstream runtime errors.

    All upstream errors carry an *http_status* that the app layer
    can use when constructing the error response.
    """

    error_code = ErrorCode.UPSTREAM_PROTOCOL_ERROR

    def __init__(self, message: str, http_status: int = 502, detail: dict | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.detail = detail


class UpstreamConnectionError(UpstreamRuntimeError):
    """Upstream server is unreachable (connection refused, DNS failure, etc.)."""

    error_code = ErrorCode.UPSTREAM_UNAVAILABLE

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=503, detail=detail)


class UpstreamTimeoutError(UpstreamRuntimeError):
    """Upstream server timed out."""

    error_code = ErrorCode.UPSTREAM_TIMEOUT

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=504, detail=detail)


class UpstreamBadRequest(UpstreamRuntimeError):
    """Upstream server rejected the request as malformed (400)."""

    error_code = ErrorCode.UNSUPPORTED_FIELD

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=400, detail=detail)


class RuntimeModelNotFound(UpstreamRuntimeError):
    """Requested model was not found on the upstream server (404)."""

    error_code = ErrorCode.MODEL_NOT_FOUND

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=404, detail=detail)


class RuntimeWarming(UpstreamRuntimeError):
    """Runtime is warming / model is not ready (409/425/429)."""

    error_code = ErrorCode.MODEL_WARMING

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=425, detail=detail)


class RuntimeUnavailable(UpstreamRuntimeError):
    """Runtime is entirely unavailable (process down, not configured, etc.)."""

    error_code = ErrorCode.RUNTIME_UNAVAILABLE

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=503, detail=detail)


class RuntimeTimeout(UpstreamRuntimeError):
    """Request to the runtime timed out."""

    error_code = ErrorCode.TIMEOUT

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=504, detail=detail)


class RuntimeBadRequest(UpstreamRuntimeError):
    """Request was rejected by the runtime (malformed, unsupported fields)."""

    error_code = ErrorCode.UNSUPPORTED_FIELD

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=400, detail=detail)


class RuntimeUpstreamError(UpstreamRuntimeError):
    """Generic upstream runtime error."""

    def __init__(self, message: str, http_status: int = 502, detail: dict | None = None):
        super().__init__(message, http_status=http_status, detail=detail)


class StreamInterrupted(UpstreamRuntimeError):
    """Upstream stream was interrupted mid-response."""

    error_code = ErrorCode.STREAM_INTERRUPTED

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=502, detail=detail)


class UpstreamHTTPError(UpstreamRuntimeError):
    """Upstream returned an unexpected HTTP status."""

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        detail: dict | None = None,
        error_code: ErrorCode = ErrorCode.UPSTREAM_PROTOCOL_ERROR,
    ):
        self.error_code = error_code
        super().__init__(message, http_status=status_code, detail=detail)


class RuntimeOverloaded(UpstreamRuntimeError):
    """Runtime concurrency limit reached — request rejected (429)."""

    error_code = ErrorCode.RUNNER_OVERLOADED

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=429, detail=detail)


class ModelNotFound(Exception):
    """Requested model cannot be found in any registered runtime."""

    def __init__(self, model_id: str, detail: str = ""):
        self.model_id = model_id
        super().__init__(
            f"Model '{safe_model_alias(model_id)}' not found. {detail}".strip()
        )

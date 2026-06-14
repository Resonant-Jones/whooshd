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


def build_forward_body(request, *, model_override: str | None = None) -> dict:
    """Build a JSON-serialisable dict from a ChatCompletionRequest.

    Preserves all recognised OpenAI-compatible fields plus any
    extra fields captured from the request body.  Fields with
    ``None`` values are omitted from the forward body.

    *model_override* allows the adapter to set a different model ID
    for the upstream server.
    """
    body: dict = {
        "model": model_override if model_override else request.model,
        "messages": [
            _serialize_message(m) for m in request.messages
        ],
        "stream": request.stream,
    }

    # ── Fields to forward when non-None ───────────────────────────
    _maybe_set(body, "temperature", request.temperature)
    _maybe_set(body, "top_p", request.top_p)
    _maybe_set(body, "max_tokens", request.max_tokens)
    _maybe_set(body, "max_completion_tokens", request.max_completion_tokens)
    _maybe_set(body, "stop", request.stop)
    _maybe_set(body, "user", request.user)

    # Tool / function calling.
    _maybe_set(body, "tools", request.tools)
    _maybe_set(body, "tool_choice", request.tool_choice)
    _maybe_set(body, "parallel_tool_calls", request.parallel_tool_calls)

    # Structured output.
    _maybe_set(body, "response_format", request.response_format)

    # Sampling parameters.
    _maybe_set(body, "seed", request.seed)
    _maybe_set(body, "presence_penalty", request.presence_penalty)
    _maybe_set(body, "frequency_penalty", request.frequency_penalty)
    _maybe_set(body, "logit_bias", request.logit_bias)
    _maybe_set(body, "logprobs", request.logprobs)
    _maybe_set(body, "top_logprobs", request.top_logprobs)

    # Reasoning.
    _maybe_set(body, "reasoning_effort", request.reasoning_effort)

    # Metadata.
    _maybe_set(body, "metadata", request.metadata)

    # Extra fields captured from the request (model_config extra='allow').
    extra = getattr(request, "extra_fields", None) or {}
    for key, value in extra.items():
        if value is not None:
            body[key] = value

    return body


def _maybe_set(d: dict, key: str, value) -> None:
    """Set *key* in *d* to *value* if value is not None."""
    if value is not None:
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
    body = build_forward_body(request, model_override=model_override)
    safe_req_headers = _filter_safe_headers(headers)
    safe_req_headers["Content-Type"] = "application/json"

    logger.info("forward.non_streaming url=%s model=%s", url, body.get("model"))
    logger.debug("forward.non_streaming.body keys=%s", list(body.keys()))

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
                f"Upstream returned 200 but response could not be parsed: {exc}",
                status_code=502,
            ) from exc

    if resp.status_code == 400:
        _detail = _safe_upstream_body(resp)
        raise UpstreamBadRequest(
            f"Upstream rejected request: {resp.status_code}",
            detail=_detail,
        )

    if resp.status_code == 404:
        raise RuntimeModelNotFound(
            f"Model not found on upstream server: {body.get('model')}"
        )

    if resp.status_code in (408, 504):
        raise UpstreamTimeoutError(
            f"Upstream server reported timeout: {resp.status_code}"
        )

    if resp.status_code == 429:
        raise RuntimeWarming(
            f"Upstream not ready (429): model may be loading."
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
    body = build_forward_body(request, model_override=model_override)
    body["stream"] = True
    safe_req_headers = _filter_safe_headers(headers)
    safe_req_headers["Content-Type"] = "application/json"
    safe_req_headers["Accept"] = "text/event-stream"

    logger.info("forward.streaming url=%s model=%s", url, body.get("model"))

    response = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, json=body, headers=safe_req_headers
            ) as response:
                if response.status_code != 200:
                    # Read error body safely.
                    try:
                        err_body = await response.aread()
                        err_text = err_body.decode("utf-8", errors="replace")[:512]
                    except Exception:
                        err_text = "(could not read error body)"
                    raise UpstreamHTTPError(
                        f"Upstream returned {response.status_code} for streaming request",
                        status_code=502,
                        detail={"upstream_status": response.status_code, "body": err_text},
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
                        yield chunk
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning(
                            "forward.streaming.parse_error line=%s error=%s",
                            data_str[:120],
                            exc,
                        )
                        # Skip unparseable chunks rather than failing the stream.
                        continue

    except GeneratorExit:
        # Client disconnected — let the upstream stream close naturally
        # by exiting the generator.
        logger.info("forward.streaming.client_disconnect")
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


def _safe_upstream_body(resp) -> dict | None:
    """Safely read the upstream response body for error detail."""
    try:
        text = resp.text[:512] if resp.text else ""
        return {"upstream_body": text, "upstream_status": resp.status_code}
    except Exception:
        return {"upstream_status": resp.status_code}


def _classify_request_exception(exc: Exception, server_url: str, timeout: float) -> UpstreamRuntimeError:
    """Classify a transport-level exception into a typed UpstreamRuntimeError.

    Uses string-based classification rather than typed except clauses
    so that tests can safely mock httpx without type errors.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    if "connect" in exc_name.lower() or "connection" in exc_msg:
        return UpstreamConnectionError(
            f"Upstream server at {server_url} is not reachable."
        )

    if "readtimeout" in exc_name.lower() or "timeout" in exc_name.lower() or "timeout" in exc_msg:
        return UpstreamTimeoutError(
            f"Upstream server at {server_url} timed out after {timeout}s."
        )

    # Generic fallback — unexpected transport error.
    return UpstreamHTTPError(
        f"Unexpected transport error communicating with {server_url}: {exc}",
        status_code=502,
    )


# ── Error classes ───────────────────────────────────────────────────────────


class UpstreamRuntimeError(Exception):
    """Base class for upstream runtime errors.

    All upstream errors carry an *http_status* that the app layer
    can use when constructing the error response.
    """

    def __init__(self, message: str, http_status: int = 502):
        super().__init__(message)
        self.http_status = http_status


class UpstreamConnectionError(UpstreamRuntimeError):
    """Upstream server is unreachable (connection refused, DNS failure, etc.)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=503)


class UpstreamTimeoutError(UpstreamRuntimeError):
    """Upstream server timed out."""

    def __init__(self, message: str):
        super().__init__(message, http_status=504)


class UpstreamBadRequest(UpstreamRuntimeError):
    """Upstream server rejected the request as malformed (400)."""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message, http_status=400)
        self.detail = detail


class RuntimeModelNotFound(UpstreamRuntimeError):
    """Requested model was not found on the upstream server (404)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=404)


class RuntimeWarming(UpstreamRuntimeError):
    """Runtime is warming / model is not ready (409/425/429)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=425)


class RuntimeUnavailable(UpstreamRuntimeError):
    """Runtime is entirely unavailable (process down, not configured, etc.)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=503)


class RuntimeTimeout(UpstreamRuntimeError):
    """Request to the runtime timed out."""

    def __init__(self, message: str):
        super().__init__(message, http_status=504)


class RuntimeBadRequest(UpstreamRuntimeError):
    """Request was rejected by the runtime (malformed, unsupported fields)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=400)


class RuntimeUpstreamError(UpstreamRuntimeError):
    """Generic upstream runtime error."""

    def __init__(self, message: str, http_status: int = 502):
        super().__init__(message, http_status=http_status)


class StreamInterrupted(UpstreamRuntimeError):
    """Upstream stream was interrupted mid-response."""

    def __init__(self, message: str):
        super().__init__(message, http_status=502)


class UpstreamHTTPError(UpstreamRuntimeError):
    """Upstream returned an unexpected HTTP status."""

    def __init__(self, message: str, status_code: int = 502, detail: dict | None = None):
        super().__init__(message, http_status=status_code)
        self.detail = detail


class RuntimeOverloaded(UpstreamRuntimeError):
    """Runtime concurrency limit reached — request rejected (429)."""

    def __init__(self, message: str):
        super().__init__(message, http_status=429)


class ModelNotFound(Exception):
    """Requested model cannot be found in any registered runtime."""

    def __init__(self, model_id: str, detail: str = ""):
        self.model_id = model_id
        super().__init__(f"Model '{model_id}' not found. {detail}".strip())

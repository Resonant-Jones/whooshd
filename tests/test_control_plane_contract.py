"""Focused tests for the versioned Whoosh'd control-plane contract."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi.responses import JSONResponse
from starlette.requests import Request

from whooshd.app import _execute_streaming, add_control_plane_version_header, app
from whooshd.contracts import ChatCompletionChunk, ChatCompletionRequest, ErrorCode
from whooshd.control_plane import (
    CONTROL_PLANE_CONTRACT_VERSION,
    CONTROL_PLANE_VERSION_HEADER,
    LEGACY_CONTROL_PLANE_CONTRACT_VERSION,
    LEGACY_CONTROL_PLANE_VERSION_HEADER,
    TARGET_CONTRACT_VERSION_HEADER,
    TARGET_CONTRACT_VERSION_VALUE,
    error_fields,
)
from whooshd.correlation import (
    UPSTREAM_REQUEST_ID_HEADER,
    WHOOSH_REQUEST_ID_HEADER,
)
from whooshd.http_forwarding import StreamInterrupted


def test_v1_error_header_and_body_contract():
    body = error_fields(
        ErrorCode.MODEL_WARMING,
        message="Model is warming",
        request_id="req-v1-7",
        details={"model_alias": "gemma", "unsafe_body": "sentinel-body"},
    )

    assert body["contract_version"] == CONTROL_PLANE_CONTRACT_VERSION
    assert body["code"] == ErrorCode.MODEL_WARMING
    assert body["http_status"] == 425
    assert body["retryable"] is True
    assert body["retry_after_seconds"] == 2.0
    assert body["request_id"] == "req-v1-7"
    assert "unsafe_body" not in body.get("details", {})


@pytest.mark.parametrize(
    ("code", "status", "retryable", "retry_after"),
    [
        (ErrorCode.MODEL_WARMING, 425, True, 2.0),
        (ErrorCode.RUNNER_OVERLOADED, 429, True, 2.0),
        (ErrorCode.QUEUE_FULL, 429, True, 2.0),
        (ErrorCode.TIMEOUT, 504, True, None),
        (ErrorCode.MODEL_NOT_FOUND, 404, False, None),
        (ErrorCode.RUNTIME_UNAVAILABLE, 503, True, None),
    ],
)
def test_runtime_failure_matrix(code, status, retryable, retry_after):
    body = error_fields(code, message="bounded diagnostic")

    assert body["http_status"] == status
    assert body["retryable"] is retryable
    assert body["retry_after_seconds"] == retry_after


@pytest.mark.asyncio
async def test_retry_after_is_present_in_body_and_http_header():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "raw_path": b"/v1/models",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1),
        "scheme": "http",
    }
    request = Request(scope)

    async def call_next(_request):
        return JSONResponse(
            status_code=429,
            content=error_fields(
                ErrorCode.QUEUE_FULL,
                message="Queue is full",
                request_id="queue-42",
            ),
        )

    response = await add_control_plane_version_header(request, call_next)

    assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION
    assert response.headers["Retry-After"] == "2"
    payload = json.loads(response.body)
    assert payload["retry_after_seconds"] == 2.0
    assert payload["request_id"] == "queue-42"


@pytest.mark.asyncio
async def test_owned_success_and_error_paths_advertise_contract_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in (
            "/health",
            "/health/runtime",
            "/ready",
            "/runtime",
            "/models",
            "/v1/models",
            "/api/tags",
            "/runtime/requests",
            "/runtime/model",
            "/runtime/admission",
            "/health/threadwake",
        ):
            response = await client.get(path)
            assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION

        response = await client.post("/v1/generate", json={})
        assert response.status_code == 422
        assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION
        assert response.json()["contract_version"] == CONTROL_PLANE_CONTRACT_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {TARGET_CONTRACT_VERSION_HEADER: TARGET_CONTRACT_VERSION_VALUE},
        {
            LEGACY_CONTROL_PLANE_VERSION_HEADER: (
                LEGACY_CONTROL_PLANE_CONTRACT_VERSION
            ),
        },
        {
            TARGET_CONTRACT_VERSION_HEADER: TARGET_CONTRACT_VERSION_VALUE,
            LEGACY_CONTROL_PLANE_VERSION_HEADER: (
                LEGACY_CONTROL_PLANE_CONTRACT_VERSION
            ),
        },
    ],
    ids=["missing", "target", "legacy", "matching-dual"],
)
async def test_supported_request_version_forms_preserve_success(headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers=headers)

    assert response.status_code == 200
    assert (
        response.headers[LEGACY_CONTROL_PLANE_VERSION_HEADER]
        == LEGACY_CONTROL_PLANE_CONTRACT_VERSION
    )
    assert TARGET_CONTRACT_VERSION_HEADER not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "bounded_version"),
    [
        ({TARGET_CONTRACT_VERSION_HEADER: "2"}, "2"),
        ({TARGET_CONTRACT_VERSION_HEADER: "malformed-api-key-secret"}, "invalid"),
        ({TARGET_CONTRACT_VERSION_HEADER: "x" * 200}, "invalid"),
        (
            {LEGACY_CONTROL_PLANE_VERSION_HEADER: "whooshd.control.v2"},
            "whooshd.control.v2",
        ),
        (
            {
                TARGET_CONTRACT_VERSION_HEADER: "1",
                LEGACY_CONTROL_PLANE_VERSION_HEADER: "whooshd.control.v2",
            },
            "conflicting",
        ),
    ],
    ids=[
        "unsupported-target",
        "unsafe-target",
        "oversized-target",
        "unsupported-legacy",
        "conflicting-dual",
    ],
)
async def test_unsupported_request_versions_are_rejected_safely(
    headers, bounded_version
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers=headers)

    assert response.status_code == 400
    assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION
    body = response.json()
    assert body["contract_version"] == CONTROL_PLANE_CONTRACT_VERSION
    assert body["code"] == "contract_version_unsupported"
    assert body["http_status"] == 400
    assert body["retryable"] is False
    assert body["details"]["received_version"] == bounded_version
    for value in headers.values():
        if bounded_version == "invalid":
            assert value not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
@pytest.mark.parametrize(
    ("upstream_id", "echoed"),
    [
        ("codexify-contract-rejection-01", True),
        ("unsafe request id with spaces", False),
    ],
    ids=["safe-upstream-id", "unsafe-upstream-id"],
)
async def test_negotiation_failure_precedes_lifecycle_and_preserves_safe_correlation(
    monkeypatch, stream, upstream_id, echoed
):
    def fail_if_lifecycle_begins(*_args, **_kwargs):
        raise AssertionError("contract rejection began a request lifecycle")

    monkeypatch.setattr(
        "whooshd.app._begin_request_lifecycle",
        fail_if_lifecycle_begins,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                TARGET_CONTRACT_VERSION_HEADER: "2",
                UPSTREAM_REQUEST_ID_HEADER: upstream_id,
            },
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "not-executed"}],
                "stream": stream,
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "contract_version_unsupported"
    assert WHOOSH_REQUEST_ID_HEADER not in response.headers
    assert "request_id" not in response.json()
    assert "not-executed" not in response.text
    if echoed:
        assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == upstream_id
        assert response.json()["upstream_request_id"] == upstream_id
    else:
        assert UPSTREAM_REQUEST_ID_HEADER not in response.headers
        assert "upstream_request_id" not in response.json()


class _FailingStreamAdapter:
    kind = "stub"

    async def chat_completion_stream(self, _request, context=None):
        _ = context
        yield ChatCompletionChunk.model_validate(
            {
                "id": "stream-1",
                "created": 1,
                "model": "stub-model",
                "choices": [{"index": 0, "delta": {"content": "visible"}}],
            }
        )
        raise StreamInterrupted("raw-upstream-body-sentinel")


class _StreamRuntime:
    def __init__(self):
        self.disconnects: list[str] = []
        self.cancelled: list[str] = []

    def record_stream_disconnect(self, request_id):
        self.disconnects.append(request_id)

    def cancel_request(self, request_id):
        self.cancelled.append(request_id)


@pytest.mark.asyncio
async def test_stream_failure_after_visible_output_has_error_without_done():
    request = ChatCompletionRequest(
        model="stub-model",
        messages=[{"role": "user", "content": "prompt-sentinel"}],
        stream=True,
    )
    runtime = _StreamRuntime()
    response = await _execute_streaming(
        _FailingStreamAdapter(), request, None, runtime, "turn-17"
    )
    chunks = [chunk async for chunk in response.body_iterator]
    wire = b"".join(
        chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in chunks
    ).decode("utf-8")

    assert "visible" in wire
    assert "stream_interrupted" in wire
    assert CONTROL_PLANE_CONTRACT_VERSION in wire
    assert "[DONE]" not in wire
    assert "raw-upstream-body-sentinel" not in wire
    assert "prompt-sentinel" not in wire
    assert runtime.disconnects == ["turn-17"]
    assert runtime.cancelled == ["turn-17"]

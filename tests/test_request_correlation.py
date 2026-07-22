"""Focused tests for bounded request correlation across Whoosh'd boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import _execute_streaming, app
from whooshd.contracts import ChatCompletionChunk, ChatCompletionRequest, ErrorCode
from whooshd.control_plane import (
    CONTROL_PLANE_CONTRACT_VERSION,
    CONTROL_PLANE_VERSION_HEADER,
    error_fields,
)
from whooshd.http_forwarding import StreamInterrupted
from whooshd.runtime import RuntimeState, get_runtime


def test_runtime_lifecycle_keeps_root_task_attempt_and_local_ids_distinct():
    runtime = RuntimeState()
    local_id = runtime.begin_request(
        model="stub-model",
        stream=False,
        correlation_id="req-root-1",
        codexify_task_id="task-1",
        codexify_attempt_id="attempt-1",
    )

    snapshot = runtime.get_request_snapshot(local_id)
    assert snapshot is not None
    assert snapshot.request_id == local_id
    assert snapshot.request_id.startswith("whooshd_")
    assert snapshot.correlation_id == "req-root-1"
    assert snapshot.codexify_task_id == "task-1"
    assert snapshot.codexify_attempt_id == "attempt-1"
    assert snapshot.request_id != snapshot.correlation_id


def test_error_metadata_drops_unbounded_or_unsafe_correlation_values():
    body = error_fields(
        ErrorCode.RUNTIME_UNAVAILABLE,
        message="bounded diagnostic",
        request_id="local-1",
        correlation_id="prompt secret",
        codexify_task_id="task-1",
        codexify_attempt_id="a" * 129,
        whooshd_request_id="whooshd-local-1",
    )

    assert body["request_id"] == "local-1"
    assert body["codexify_task_id"] == "task-1"
    assert body["whooshd_request_id"] == "whooshd-local-1"
    assert body["correlation_id"] is None
    assert body["codexify_attempt_id"] is None
    assert "prompt secret" not in json.dumps(body)


@pytest.mark.asyncio
async def test_success_headers_echo_bounded_ids_and_contract_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={
                CONTROL_PLANE_VERSION_HEADER: CONTROL_PLANE_CONTRACT_VERSION,
                "X-Request-ID": "req-root-2",
                "X-Codexify-Task-ID": "task-2",
                "X-Codexify-Attempt-ID": "attempt-2",
            },
        )

    assert response.status_code == 200
    assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION
    assert response.headers["X-Request-ID"] == "req-root-2"
    assert response.headers["X-Codexify-Task-ID"] == "task-2"
    assert response.headers["X-Codexify-Attempt-ID"] == "attempt-2"
    assert "X-Whooshd-Request-ID" not in response.headers


@pytest.mark.asyncio
async def test_chat_success_headers_include_root_and_local_lifecycle_ids():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                CONTROL_PLANE_VERSION_HEADER: CONTROL_PLANE_CONTRACT_VERSION,
                "X-Request-ID": "req-root-chat",
                "X-Codexify-Task-ID": "task-chat",
                "X-Codexify-Attempt-ID": "attempt-chat",
            },
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "correlation-test"}],
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-root-chat"
    local_id = response.headers["X-Whooshd-Request-ID"]
    assert local_id.startswith("whooshd_")
    assert response.headers["X-Codexify-Task-ID"] == "task-chat"
    assert response.headers["X-Codexify-Attempt-ID"] == "attempt-chat"
    provenance = response.json()["runtime_provenance"]
    assert provenance["correlation_id"] == "req-root-chat"
    assert provenance["codexify_task_id"] == "task-chat"
    assert provenance["codexify_attempt_id"] == "attempt-chat"
    assert provenance["whooshd_request_id"] == local_id


@pytest.mark.asyncio
async def test_unsafe_incoming_ids_are_not_echoed():
    transport = ASGITransport(app=app)
    unsafe_root = "prompt secret"
    unsafe_task = "task/with/path"
    oversized_attempt = "a" * 129
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={
                "X-Request-ID": unsafe_root,
                "X-Codexify-Task-ID": unsafe_task,
                "X-Codexify-Attempt-ID": oversized_attempt,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != unsafe_root
    assert "X-Codexify-Task-ID" not in response.headers
    assert "X-Codexify-Attempt-ID" not in response.headers
    assert unsafe_root not in response.text
    assert unsafe_task not in response.text
    assert oversized_attempt not in response.text


@pytest.mark.asyncio
async def test_unsafe_cancel_path_is_not_echoed():
    unsafe_request_id = "prompt secret"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/runtime/requests/{unsafe_request_id}/cancel"
        )

    assert response.status_code == 404
    assert unsafe_request_id not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["whooshd.control.v2", "x" * 300])
async def test_explicit_unsupported_version_is_rejected_without_echoing_raw_header(version):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={
                CONTROL_PLANE_VERSION_HEADER: version,
                "X-Request-ID": "req-root-3",
            },
        )

    assert response.status_code == 400
    assert response.headers[CONTROL_PLANE_VERSION_HEADER] == CONTROL_PLANE_CONTRACT_VERSION
    assert response.headers["X-Request-ID"] == "req-root-3"
    body = response.json()
    assert body["code"] == "contract_version_unsupported"
    assert body["http_status"] == 400
    assert body["retryable"] is False
    if len(version) > 128:
        assert version not in response.text
    else:
        assert body["details"]["received_version"] == version


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
async def test_stream_error_preserves_correlation_and_never_emits_done():
    request = ChatCompletionRequest(
        model="stub-model",
        messages=[{"role": "user", "content": "prompt-sentinel"}],
        stream=True,
    )
    runtime = _StreamRuntime()
    context = SimpleNamespace(
        correlation_id="req-root-4",
        codexify_task_id="task-4",
        codexify_attempt_id="attempt-4",
    )
    response = await _execute_streaming(
        _FailingStreamAdapter(),
        request,
        context,
        runtime,
        "whooshd-local-4",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    wire = b"".join(
        chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
        for chunk in chunks
    ).decode("utf-8")

    assert "visible" in wire
    assert "stream_interrupted" in wire
    assert "raw-upstream-body-sentinel" not in wire
    assert "prompt-sentinel" not in wire
    assert "req-root-4" in wire
    assert "task-4" in wire
    assert "attempt-4" in wire
    assert "[DONE]" not in wire
    assert runtime.disconnects == ["whooshd-local-4"]
    assert runtime.cancelled == ["whooshd-local-4"]


@pytest.mark.asyncio
async def test_cancellation_response_uses_lifecycle_correlation():
    runtime = get_runtime()
    local_id = runtime.begin_request(
        model="stub-model",
        stream=True,
        correlation_id="req-root-cancel",
        codexify_task_id="task-cancel",
        codexify_attempt_id="attempt-cancel",
    )
    runtime.mark_streaming(local_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/runtime/requests/{local_id}/cancel",
            headers={"X-Request-ID": "req-cancel-operation"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-root-cancel"
    assert response.headers["X-Whooshd-Request-ID"] == local_id
    assert response.headers["X-Codexify-Task-ID"] == "task-cancel"
    assert response.headers["X-Codexify-Attempt-ID"] == "attempt-cancel"
    body = response.json()
    assert body["request_id"] == local_id
    assert body["correlation_id"] == "req-root-cancel"
    assert body["whooshd_request_id"] == local_id

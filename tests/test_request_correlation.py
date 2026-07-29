"""Focused bounded request-correlation coverage for CWC-008."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import _execute_streaming, app
from whooshd.correlation import (
    MAX_IDENTIFIER_LENGTH,
    UPSTREAM_REQUEST_ID_HEADER,
    WHOOSH_REQUEST_ID_HEADER,
    correlation_response_headers,
    generate_whoosh_request_id,
    normalize_identifier,
)
from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    RequestExecutionContext,
)
from whooshd.http_forwarding import RuntimeUnavailable, StreamInterrupted
from whooshd.runtime import RuntimeState, get_runtime


@pytest.fixture(autouse=True)
def _reset_runtime_and_queue():
    import whooshd.queue as queue_module
    import whooshd.runtime as runtime_module

    queue_module._queue = None
    runtime_module._runtime = None
    yield
    queue_module._queue = None
    runtime_module._runtime = None


def _payload(*, stream: bool = False, content: str = "correlation test") -> dict:
    return {
        "model": "stub-model",
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
    }


def test_identifier_validation_rejects_unsafe_or_oversized_values():
    valid = "codexify.request_01:attempt-2"

    assert normalize_identifier(valid) == valid
    assert normalize_identifier(" leading-space") is None
    assert normalize_identifier("contains/slash") is None
    assert normalize_identifier("has whitespace") is None
    assert normalize_identifier("x" * (MAX_IDENTIFIER_LENGTH + 1)) is None

    local_id = generate_whoosh_request_id()
    assert local_id.startswith("whoosh-")
    assert normalize_identifier(local_id) == local_id

    headers = correlation_response_headers(
        upstream_request_id="unsafe/body",
        whoosh_request_id=local_id,
    )
    assert headers == {WHOOSH_REQUEST_ID_HEADER: local_id}


def test_lifecycle_snapshot_keeps_upstream_and_whoosh_ids_distinct():
    runtime = RuntimeState()
    whoosh_id = runtime.begin_request(
        model="stub-model",
        stream=False,
        upstream_request_id="codexify-request-01",
    )

    snapshot = runtime.get_request_snapshot(whoosh_id)
    assert snapshot is not None
    assert snapshot.request_id == whoosh_id
    assert snapshot.request_id.startswith("whoosh-")
    assert snapshot.upstream_request_id == "codexify-request-01"
    assert snapshot.request_id != snapshot.upstream_request_id

    unsafe_id = runtime.begin_request(
        model="stub-model",
        stream=False,
        upstream_request_id="prompt secret/never-store",
    )
    unsafe_snapshot = runtime.get_request_snapshot(unsafe_id)
    assert unsafe_snapshot is not None
    assert unsafe_snapshot.upstream_request_id is None
    assert "prompt secret/never-store" not in unsafe_snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_immediate_non_streaming_success_echoes_both_distinct_ids():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-immediate-01"},
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-immediate-01"
    whoosh_id = response.headers[WHOOSH_REQUEST_ID_HEADER]
    assert whoosh_id.startswith("whoosh-")
    assert whoosh_id != response.headers[UPSTREAM_REQUEST_ID_HEADER]
    provenance = response.json()["runtime_provenance"]
    assert provenance["request_id"] == whoosh_id
    assert provenance["upstream_request_id"] == "codexify-immediate-01"


@pytest.mark.asyncio
async def test_streaming_success_sets_both_ids_before_visible_output():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-stream-01"},
            json=_payload(stream=True),
        ) as response:
            assert response.status_code == 200
            assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-stream-01"
            whoosh_id = response.headers[WHOOSH_REQUEST_ID_HEADER]
            assert whoosh_id.startswith("whoosh-")
            wire = "\n".join([line async for line in response.aiter_lines()])

    assert "data: [DONE]" in wire


@pytest.mark.asyncio
async def test_admission_rejection_before_lifecycle_exposes_only_upstream_id(monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    runtime = get_runtime()
    blocker = runtime.begin_request(model="stub-model", stream=False)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-rejected-01"},
                json=_payload(),
            )
    finally:
        runtime.complete_request(blocker)

    assert response.status_code == 429
    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-rejected-01"
    assert WHOOSH_REQUEST_ID_HEADER not in response.headers
    body = response.json()
    assert body["upstream_request_id"] == "codexify-rejected-01"
    assert "request_id" not in body


@pytest.mark.asyncio
async def test_queued_request_retains_its_correlation_pair(monkeypatch):
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5")
    runtime = get_runtime()
    blocker = runtime.begin_request(model="stub-model", stream=False)
    runtime.mark_running(blocker)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        task = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-queued-01"},
                json=_payload(),
            )
        )
        await asyncio.sleep(0.05)
        queued = [
            item
            for item in runtime.get_all_requests()
            if item.upstream_request_id == "codexify-queued-01"
        ]
        assert len(queued) == 1
        assert queued[0].request_id.startswith("whoosh-")

        runtime.complete_request(blocker)
        from whooshd.queue import get_queue

        get_queue().notify_capacity()
        response = await asyncio.wait_for(task, timeout=2)

    assert response.status_code == 200
    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-queued-01"
    assert response.headers[WHOOSH_REQUEST_ID_HEADER] == queued[0].request_id


@pytest.mark.asyncio
async def test_cancellation_returns_target_request_correlation_pair():
    runtime = get_runtime()
    whoosh_id = runtime.begin_request(
        model="stub-model",
        stream=True,
        upstream_request_id="codexify-cancel-target-01",
    )
    runtime.mark_streaming(whoosh_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/runtime/requests/{whoosh_id}/cancel",
            headers={UPSTREAM_REQUEST_ID_HEADER: "cancellation-operation-01"},
        )

    assert response.status_code == 200
    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-cancel-target-01"
    assert response.headers[WHOOSH_REQUEST_ID_HEADER] == whoosh_id
    assert response.json()["request_id"] == whoosh_id
    assert response.json()["upstream_request_id"] == "codexify-cancel-target-01"


class _PreStreamFailureAdapter:
    kind = "stub"

    async def chat_completion_stream(self, _request, context=None):
        _ = context
        raise RuntimeUnavailable("raw-upstream-prestream-sentinel")
        yield  # pragma: no cover - make this an async generator


class _MidStreamFailureAdapter:
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
        raise StreamInterrupted("raw-upstream-midstream-sentinel")


@pytest.mark.asyncio
async def test_pre_stream_failure_preserves_pair_in_headers_and_error_body():
    runtime = RuntimeState()
    whoosh_id = runtime.begin_request(
        model="stub-model",
        stream=True,
        upstream_request_id="codexify-prestream-01",
    )
    context = RequestExecutionContext(
        whoosh_id,
        runtime.get_cancellation_token(whoosh_id),
        stream=True,
        upstream_request_id="codexify-prestream-01",
    )
    response = await _execute_streaming(
        _PreStreamFailureAdapter(),
        ChatCompletionRequest.model_validate(_payload(stream=True)),
        context,
        runtime,
        whoosh_id,
    )

    assert response.status_code == 503
    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-prestream-01"
    assert response.headers[WHOOSH_REQUEST_ID_HEADER] == whoosh_id
    body = json.loads(response.body)
    assert body["request_id"] == whoosh_id
    assert body["upstream_request_id"] == "codexify-prestream-01"
    assert "raw-upstream-prestream-sentinel" not in response.body.decode()


@pytest.mark.asyncio
async def test_mid_stream_failure_preserves_pair_and_never_emits_done():
    runtime = RuntimeState()
    whoosh_id = runtime.begin_request(
        model="stub-model",
        stream=True,
        upstream_request_id="codexify-midstream-01",
    )
    context = RequestExecutionContext(
        whoosh_id,
        runtime.get_cancellation_token(whoosh_id),
        stream=True,
        upstream_request_id="codexify-midstream-01",
    )
    response = await _execute_streaming(
        _MidStreamFailureAdapter(),
        ChatCompletionRequest.model_validate(_payload(stream=True, content="prompt-sentinel")),
        context,
        runtime,
        whoosh_id,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    wire = b"".join(
        chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in chunks
    ).decode("utf-8")

    assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == "codexify-midstream-01"
    assert response.headers[WHOOSH_REQUEST_ID_HEADER] == whoosh_id
    assert "visible" in wire
    assert "stream_interrupted" in wire
    assert '"upstream_request_id": "codexify-midstream-01"' in wire
    assert '"request_id": "' + whoosh_id + '"' in wire
    assert "[DONE]" not in wire
    assert "raw-upstream-midstream-sentinel" not in wire
    assert "prompt-sentinel" not in wire


@pytest.mark.asyncio
async def test_unsafe_upstream_id_never_leaks_from_success_or_admission_error(monkeypatch):
    unsafe = "prompt secret/unsafe-id"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        success = await client.post(
            "/v1/chat/completions",
            headers={UPSTREAM_REQUEST_ID_HEADER: unsafe},
            json=_payload(content="body-sentinel"),
        )

    assert success.status_code == 200
    assert UPSTREAM_REQUEST_ID_HEADER not in success.headers
    assert unsafe not in success.text
    whoosh_id = success.headers[WHOOSH_REQUEST_ID_HEADER]
    snapshot = get_runtime().get_request_snapshot(whoosh_id)
    assert snapshot is not None
    assert snapshot.upstream_request_id is None

    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    blocker = get_runtime().begin_request(model="stub-model", stream=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post(
                "/v1/chat/completions",
                headers={UPSTREAM_REQUEST_ID_HEADER: unsafe},
                json=_payload(),
            )
    finally:
        get_runtime().complete_request(blocker)

    assert rejected.status_code == 429
    assert UPSTREAM_REQUEST_ID_HEADER not in rejected.headers
    assert WHOOSH_REQUEST_ID_HEADER not in rejected.headers
    assert unsafe not in rejected.text


@pytest.mark.asyncio
async def test_legacy_chat_without_upstream_id_remains_supported():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json=_payload())

    assert response.status_code == 200
    assert UPSTREAM_REQUEST_ID_HEADER not in response.headers
    assert response.headers[WHOOSH_REQUEST_ID_HEADER].startswith("whoosh-")
    assert response.json()["runtime_provenance"]["upstream_request_id"] is None


@pytest.mark.asyncio
async def test_batched_requests_do_not_cross_correlation_pairs(monkeypatch):
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_MIN_SIZE", "2")

    runtime = get_runtime()
    blocker = runtime.begin_request(model="stub-model", stream=False)
    runtime.mark_running(blocker)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        task_a = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-batch-a"},
                json=_payload(content="a"),
            )
        )
        task_b = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={UPSTREAM_REQUEST_ID_HEADER: "codexify-batch-b"},
                json=_payload(content="b"),
            )
        )
        await asyncio.sleep(0.05)
        runtime.complete_request(blocker)

        response_a, response_b = await asyncio.gather(task_a, task_b)

    for response, upstream in (
        (response_a, "codexify-batch-a"),
        (response_b, "codexify-batch-b"),
    ):
        assert response.status_code == 200
        assert response.headers[UPSTREAM_REQUEST_ID_HEADER] == upstream
        assert response.json()["runtime_provenance"]["upstream_request_id"] == upstream
        assert response.json()["runtime_provenance"]["request_id"] == response.headers[
            WHOOSH_REQUEST_ID_HEADER
        ]

    assert response_a.headers[WHOOSH_REQUEST_ID_HEADER] != response_b.headers[
        WHOOSH_REQUEST_ID_HEADER
    ]

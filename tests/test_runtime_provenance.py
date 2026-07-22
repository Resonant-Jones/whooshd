"""Focused tests for the bounded CWC-008 runtime provenance contract."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.adapters.stub import StubInferenceAdapter
from whooshd.app import app
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatMessage,
    GenerateRequest,
    WHOOSHD_RUNTIME_PROVENANCE_HEADER,
)
from whooshd.routing import RuntimeRouter, get_router, inventory_provenance, reset_router
from whooshd.runtime import RuntimeState


def _chat_request(model: str = "requested-model", *, stream: bool = False):
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="sentinel prompt")],
        stream=stream,
    )


def test_inventory_provenance_is_bounded_and_path_free():
    provenance = inventory_provenance(
        model_id="/private/model.gguf",
        runtime_kind="llama_cpp",
        resolution_source="external_route",
        loaded=False,
    )

    serialized = provenance.model_dump_json()
    assert "/private/model.gguf" not in serialized
    assert "runtime_kind" in serialized
    assert provenance.execution_mode == "external_sidecar"


def test_stub_resolution_preserves_request_and_selected_runtime_evidence(monkeypatch):
    monkeypatch.setattr("whooshd.config.get_adapter_backend", lambda: "stub")
    router = RuntimeRouter()
    router.register(StubInferenceAdapter())

    async def _run():
        resolution = await router.resolve_model_runtime("requested-model")
        assert resolution.adapter.kind == "stub"
        assert resolution.resolution_source == "configured_stub"
        provenance = resolution.provenance(
            request_id="request-7",
            backend_reported_model_id="backend-model",
        )
        assert provenance.requested_model_id == "requested-model"
        assert provenance.resolved_model_id == "stub-model"
        assert provenance.backend_reported_model_id == "backend-model"
        assert provenance.execution_mode == "stub"

    asyncio.run(_run())


def test_chat_and_generate_results_carry_actual_adapter_provenance(monkeypatch):
    monkeypatch.setattr("whooshd.config.get_adapter_backend", lambda: "stub")
    router = RuntimeRouter()
    router.register(StubInferenceAdapter())

    async def _run():
        chat = await router.chat_completion(_chat_request())
        assert chat.runtime_provenance is not None
        assert chat.runtime_provenance.adapter_name == "stub"
        assert chat.runtime_provenance.runtime_kind == "stub"
        assert chat.runtime_provenance.streaming is False

        generated = await router.generate(
            GenerateRequest(
                prompt="sentinel prompt",
                model_id="requested-model",
                request_id="generate-request-7",
            )
        )
        assert generated.runtime.provenance is not None
        assert generated.runtime.provenance.adapter_name == "stub"
        assert generated.runtime.provenance.backend_reported_model_id == "requested-model"
        assert generated.runtime.provenance.request_id == "generate-request-7"

    asyncio.run(_run())


def test_streaming_provenance_is_on_first_chunk_and_matches_stream_adapter(monkeypatch):
    monkeypatch.setattr("whooshd.config.get_adapter_backend", lambda: "stub")
    router = RuntimeRouter()
    router.register(StubInferenceAdapter())

    async def _run():
        chunks = [chunk async for chunk in router.chat_completion_stream(_chat_request(stream=True))]
        assert chunks
        assert chunks[0].runtime_provenance is not None
        assert chunks[0].runtime_provenance.streaming is True
        assert chunks[0].runtime_provenance.adapter_name == "stub"
        assert all(chunk.runtime_provenance is None for chunk in chunks[1:])

    asyncio.run(_run())


def test_native_model_inventory_includes_provenance_without_response_content():
    reset_router()
    router = get_router()
    router.register(StubInferenceAdapter())
    runtime = RuntimeState()

    async def _run():
        models = await runtime.list_models_async()
        assert models
        provenance = models[0].runtime_provenance
        assert provenance is not None
        assert provenance.runtime_kind == "stub"
        assert "sentinel" not in provenance.model_dump_json()

        openai_models = await runtime.build_openai_model_list()
        metadata = openai_models.data[0].metadata or {}
        assert metadata["runtime_provenance"]["schema_version"] == "whooshd.runtime.v1"

    try:
        asyncio.run(_run())
    finally:
        reset_router()


@pytest.mark.asyncio
async def test_http_stream_uses_bounded_header_without_changing_sse_body(monkeypatch):
    monkeypatch.setattr("whooshd.config.get_adapter_backend", lambda: "stub")
    reset_router()
    router = get_router()
    router.register(StubInferenceAdapter())

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "requested-model",
                    "messages": [{"role": "user", "content": "sentinel prompt"}],
                    "stream": True,
                },
            )

        assert response.status_code == 200
        header = response.headers[WHOOSHD_RUNTIME_PROVENANCE_HEADER]
        provenance = json.loads(header)
        assert provenance["schema_version"] == "whooshd.runtime.v1"
        assert provenance["streaming"] is True
        assert provenance["request_id"]
        assert "runtime_provenance" not in response.text
        assert response.text.rstrip().endswith("data: [DONE]")
    finally:
        reset_router()

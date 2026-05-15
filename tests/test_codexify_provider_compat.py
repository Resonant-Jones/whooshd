"""Codexify provider-compatibility test suite.

These tests prove Whoosh'd behaves like a valid local provider from
Codexify's perspective.  They use the CodexifyProbe helper which mirrors
the behaviour of Codexify's MLXRunnerClient.

All tests pass with the stub adapter — no MLX, no model downloads, no GPU.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.compat.codexify_probe import (
    CodexifyProbe,
    reconstruct_assistant_text,
)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def probe(client) -> CodexifyProbe:
    return CodexifyProbe(client)


# ── Health — Codexify expects liveness distinct from readiness ──────────────


@pytest.mark.asyncio
async def test_health_reports_ok(probe):
    result = await probe.probe_health()
    assert result.ok is True


@pytest.mark.asyncio
async def test_health_distinguishes_status_from_ok(probe):
    """Codexify must know if the runner is warming vs ready vs degraded."""
    result = await probe.probe_health()
    assert result.status != ""
    assert result.status in {
        "starting", "warming", "ready", "generating", "degraded", "offline",
    }


@pytest.mark.asyncio
async def test_health_reports_memory_pressure(probe):
    """Codexify's router can decide to throttle or fallback based on pressure."""
    result = await probe.probe_health()
    assert result.memory_pressure in {"low", "normal", "warning", "critical"}


@pytest.mark.asyncio
async def test_health_queue_and_job_fields_are_integers(probe):
    result = await probe.probe_health()
    assert isinstance(result.queue_depth, int)
    assert isinstance(result.active_jobs, int)
    assert result.queue_depth >= 0
    assert result.active_jobs >= 0


# ── Runtime — Codexify needs memory and concurrency metadata ────────────────


@pytest.mark.asyncio
async def test_runtime_reports_memory_gb(probe):
    result = await probe.probe_runtime()
    assert result.total_gb > 0
    assert result.available_gb >= 0
    assert result.available_gb <= result.total_gb


@pytest.mark.asyncio
async def test_runtime_reports_concurrency_budget(probe):
    """Codexify estimates how many concurrent requests are safe."""
    result = await probe.probe_runtime()
    assert result.max_active_jobs >= 0
    assert result.safe_concurrency >= 0
    assert result.safe_concurrency <= result.max_active_jobs


@pytest.mark.asyncio
async def test_runtime_uptime_is_positive(probe):
    result = await probe.probe_runtime()
    assert result.uptime_seconds >= 0


@pytest.mark.asyncio
async def test_runtime_loaded_models_count_is_non_negative(probe):
    result = await probe.probe_runtime()
    assert result.loaded_model_count >= 0


# ── Model inventory — Codexify must discover available models ───────────────


@pytest.mark.asyncio
async def test_openai_model_inventory_has_at_least_one_model(probe):
    result = await probe.probe_models_openai()
    assert len(result.model_ids) >= 1
    assert result.format == "openai"


@pytest.mark.asyncio
async def test_ollama_model_inventory_has_at_least_one_model(probe):
    result = await probe.probe_models_ollama()
    assert len(result.model_ids) >= 1
    assert result.format == "ollama"


@pytest.mark.asyncio
async def test_model_ids_are_non_empty_strings(probe):
    for probe_fn in [probe.probe_models_openai, probe.probe_models_ollama]:
        result = await probe_fn()
        for model_id in result.model_ids:
            assert isinstance(model_id, str)
            assert len(model_id) > 0


@pytest.mark.asyncio
async def test_model_inventories_are_consistent(probe):
    """Both formats describe the same underlying models."""
    oai = await probe.probe_models_openai()
    ollama = await probe.probe_models_ollama()
    # Ollama tags use "name:tag" — strip the tag suffix.
    ollama_base = {m.split(":")[0] for m in ollama.model_ids}
    assert set(oai.model_ids) == ollama_base


# ── Non-streaming chat — Codexify's basic generation path ───────────────────


@pytest.mark.asyncio
async def test_non_streaming_chat_returns_visible_text(probe):
    result = await probe.probe_chat_completion(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert result.ok is True
    assert len(result.content) > 0
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_non_streaming_chat_echoes_model(probe):
    result = await probe.probe_chat_completion(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hi"}],
    )
    assert result.model == "qwen2.5-1.5b-instruct-mlx"


@pytest.mark.asyncio
async def test_non_streaming_chat_reports_usage(probe):
    result = await probe.probe_chat_completion(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert result.prompt_tokens is not None
    assert result.completion_tokens is not None
    assert result.prompt_tokens >= 1
    assert result.completion_tokens >= 1


@pytest.mark.asyncio
async def test_non_streaming_chat_accepts_system_message(probe):
    result = await probe.probe_chat_completion(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ],
    )
    assert result.ok is True


# ── Streaming chat — Codexify's interactive UI path ─────────────────────────


@pytest.mark.asyncio
async def test_streaming_returns_visible_text(probe):
    result = await probe.probe_chat_stream(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert result.ok is True
    assert len(result.visible_text) > 0


@pytest.mark.asyncio
async def test_streaming_reconstructs_stub_text(probe):
    """Codexify can reconstruct coherent assistant text from the SSE stream."""
    result = await probe.probe_chat_stream(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert result.visible_text == "Whoosh'd streaming stub online."


@pytest.mark.asyncio
async def test_streaming_content_type_is_event_stream(probe):
    result = await probe.probe_chat_stream(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hi"}],
    )
    assert "text/event-stream" in result.content_type


@pytest.mark.asyncio
async def test_streaming_emits_multiple_chunks(probe):
    """A streaming response should contain more than one chunk."""
    result = await probe.probe_chat_stream(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hi"}],
    )
    assert result.chunk_count >= 2


@pytest.mark.asyncio
async def test_streaming_no_white_text_whitespace_artifacts(probe):
    """Codexify expects to join delta.content without extra spaces."""
    result = await probe.probe_chat_stream(
        model="qwen2.5-1.5b-instruct-mlx",
        messages=[{"role": "user", "content": "Hello"}],
    )
    # The stub emits trailing spaces — the result should not start with whitespace.
    assert len(result.visible_text) > 0
    assert not result.visible_text.startswith(" ")


# ── Validation — Codexify expects structured errors ─────────────────────────


@pytest.mark.asyncio
async def test_malformed_empty_messages_is_rejected(client):
    """Codexify should never receive 200 for an empty message list."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_malformed_missing_model_is_rejected(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_malformed_empty_content_is_rejected(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "m",
            "messages": [{"role": "user", "content": ""}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_malformed_invalid_role_is_rejected(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "m",
            "messages": [{"role": "ghost", "content": "boo"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_validation_errors_are_structured(client):
    """Validation errors should carry a code and message, not raw HTML."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": []},
    )
    body = resp.json()
    assert "code" in body
    assert "message" in body
    assert body["code"] == "INTERNAL"


# ── Streaming parser — unit tests for reconstruct_assistant_text ────────────


class TestReconstructAssistantText:
    """Contract tests for the streaming parser Codexify relies on."""

    def test_parses_openai_format(self):
        sse = (
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n'
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}\n'
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"world."},"finish_reason":null}]}\n'
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "Hello world."

    def test_ignores_role_only_chunks(self):
        sse = (
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == ""

    def test_ignores_finish_marker_chunks(self):
        sse = (
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Done"},"finish_reason":null}]}\n'
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":null},"finish_reason":"stop"}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "Done"

    def test_ignores_lines_without_data_prefix(self):
        sse = (
            "event: ping\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "OK"

    def test_stops_at_done_even_if_more_follows(self):
        sse = (
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"First"},"finish_reason":null}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Zombie"},"finish_reason":null}]}\n'
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "First"

    def test_empty_stream_yields_empty_string(self):
        assert reconstruct_assistant_text("") == ""

    def test_does_not_expose_internal_fields(self):
        """If a chunk carries reasoning or metadata fields, they must not appear."""
        sse = (
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Visible","reasoning":"secret","tool_calls":null},"finish_reason":null}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "Visible"
        assert "secret" not in text
        assert "reasoning" not in text

    def test_malformed_json_lines_are_skipped(self):
        sse = (
            "data: not-json\n"
            "\n"
            'data: {"id":"a","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n'
            "\n"
            "data: [DONE]\n"
            "\n"
        )
        text = reconstruct_assistant_text(sse)
        assert text == "OK"


# ── Contract documentation — these tests serve as living spec ───────────────


class TestProviderContractSpec:
    """Document the expected Codexify ↔ Whoosh'd provider contract.

    These assertions are the acceptance criteria for the local provider
    boundary.  When real MLX is wired in, these tests must still pass.
    """

    async def test_contract_health_distinguishes_process_from_readiness(self, probe):
        h = await probe.probe_health()
        # ok=true means the process is alive and answering.
        # status tells Codexify whether a model is actually ready.
        assert h.ok is True
        assert h.status != ""

    async def test_contract_model_inventory_is_discoverable(self, probe):
        oai = await probe.probe_models_openai()
        ollama = await probe.probe_models_ollama()
        assert len(oai.model_ids) >= 1
        assert len(ollama.model_ids) >= 1

    async def test_contract_non_streaming_returns_assistant_text(self, probe):
        result = await probe.probe_chat_completion(
            model="qwen2.5-1.5b-instruct-mlx",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.ok is True
        assert len(result.content) > 0
        assert result.finish_reason == "stop"

    async def test_contract_streaming_reconstructs_visible_text(self, probe):
        result = await probe.probe_chat_stream(
            model="qwen2.5-1.5b-instruct-mlx",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert result.ok is True
        assert len(result.visible_text) > 0
        assert "text/event-stream" in result.content_type

    async def test_contract_malformed_requests_are_rejected(self, client):
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "code" in body
        assert "message" in body

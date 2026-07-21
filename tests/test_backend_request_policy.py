"""Focused CWC-006 tests for the ingress-to-backend boundary."""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.backend_request_policy import (
    BackendRequestPolicyError,
    BackendChatRequest,
    ensure_backend_chat_request,
    ensure_backend_generate_request,
    sanitize_chat_request,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage, GenerateRequest
from whooshd.http_forwarding import build_forward_body
from whooshd.app import app


PROMPT_SENTINEL = "PROMPT_SENTINEL_CWC006"
ASSISTANT_SENTINEL = "ASSISTANT_SENTINEL_CWC006"
METADATA_SECRET = "METADATA_SECRET_CWC006"
TOOL_SECRET = "TOOL_ARGUMENT_SECRET_CWC006"


def _request(**kwargs) -> ChatCompletionRequest:
    values = {
        "model": "gemma-4-12b-it-qat-4bit",
        "messages": [ChatMessage(role="user", content=PROMPT_SENTINEL)],
        "stream": False,
    }
    values.update(kwargs)
    return ChatCompletionRequest(**values)


def test_threadwake_is_internal_and_absent_from_http_body():
    request = _request(threadwake={"enabled": True, "mode": "observe"})

    backend = sanitize_chat_request(request, adapter_kind="mlx_vlm")
    body = build_forward_body(backend, adapter_kind="mlx_vlm")

    assert not hasattr(backend, "threadwake")
    assert "threadwake" not in body
    assert request.threadwake == {"enabled": True, "mode": "observe"}


def test_metadata_is_not_forwarded_without_declared_support():
    request = _request(metadata={"source": METADATA_SECRET})
    backend = sanitize_chat_request(request, adapter_kind="mlx_lm_server")

    assert not hasattr(backend, "metadata")
    assert "metadata" not in build_forward_body(backend)


def test_reserved_control_metadata_never_reaches_backend():
    request = _request(
        codexify_provenance={"secret": METADATA_SECRET},
        whooshd_routing_hint="internal",
        threadwake_segments=[{"text": PROMPT_SENTINEL}],
    )

    backend = sanitize_chat_request(request, adapter_kind="llama_cpp")
    body = build_forward_body(backend, adapter_kind="llama_cpp")

    assert backend.stripped_fields == (
        "codexify_provenance",
        "threadwake_segments",
        "whooshd_routing_hint",
    )
    assert all(name not in body for name in request.extra_fields)


def test_unknown_extras_are_stripped_without_inspecting_values():
    request = _request(
        arbitrary_extra={"nested": TOOL_SECRET},
        another_extra=ASSISTANT_SENTINEL,
    )
    backend = sanitize_chat_request(request, adapter_kind="stub")

    assert backend.extra_fields == {}
    assert ASSISTANT_SENTINEL not in repr(build_forward_body(backend))
    assert TOOL_SECRET not in repr(build_forward_body(backend))


def test_explicit_llama_extension_is_forwarded():
    request = _request(top_k=40, min_p=0.05, repeat_penalty=1.1)
    backend = sanitize_chat_request(request, adapter_kind="llama_cpp")
    body = build_forward_body(backend, adapter_kind="llama_cpp")

    assert body["top_k"] == 40
    assert body["min_p"] == 0.05
    assert body["repeat_penalty"] == 1.1


def test_extension_is_not_forwarded_to_another_adapter():
    request = _request(top_k=40)
    with pytest.raises(BackendRequestPolicyError):
        sanitize_chat_request(request, adapter_kind="mlx_lm_server")


def test_standard_sampling_and_tool_fields_remain_intact():
    tools = [{"type": "function", "function": {"name": "lookup"}}]
    request = _request(
        temperature=0.2,
        top_p=0.8,
        max_tokens=512,
        stop=["END"],
        tools=tools,
        tool_choice="auto",
        response_format={"type": "json_object"},
    )
    body = build_forward_body(
        sanitize_chat_request(request, adapter_kind="llama_cpp"),
        adapter_kind="llama_cpp",
    )

    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.8
    assert body["max_tokens"] == 512
    assert body["stop"] == ["END"]
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["response_format"] == {"type": "json_object"}


def test_inference_affecting_unknown_field_is_rejected_without_value():
    request = _request(unknown_temperature=METADATA_SECRET)

    with pytest.raises(BackendRequestPolicyError) as exc_info:
        sanitize_chat_request(request, adapter_kind="generic")

    assert exc_info.value.rejected_fields == ("unknown_temperature",)
    assert METADATA_SECRET not in str(exc_info.value)


def test_streaming_and_non_streaming_share_the_same_policy():
    request = _request(
        stream=True,
        metadata={"secret": METADATA_SECRET},
        custom_control="ignored",
    )
    backend = sanitize_chat_request(request, adapter_kind="mlx_lm_server")
    streaming_body = build_forward_body(backend, adapter_kind="mlx_lm_server")
    non_streaming_body = dict(streaming_body)
    non_streaming_body["stream"] = False

    assert streaming_body["stream"] is True
    assert non_streaming_body["stream"] is False
    assert "metadata" not in streaming_body
    assert "custom_control" not in streaming_body
    assert "metadata" not in non_streaming_body


def test_queued_and_batch_representations_are_sanitized_and_idempotent():
    request = _request(
        metadata={"secret": METADATA_SECRET},
        threadwake={"enabled": True},
        custom_control="ignored",
    )
    queued = sanitize_chat_request(request, adapter_kind="stub")
    batch = [ensure_backend_chat_request(queued, adapter_kind="stub")]

    assert all(isinstance(item, BackendChatRequest) for item in batch)
    assert all(not hasattr(item, "threadwake") for item in batch)
    assert all("metadata" not in build_forward_body(item) for item in batch)
    assert all("custom_control" not in build_forward_body(item) for item in batch)


def test_fallback_receives_same_sanitized_request():
    request = _request(metadata={"secret": METADATA_SECRET}, threadwake={"enabled": True})
    backend = sanitize_chat_request(request, adapter_kind="llama_cpp")

    captured = []

    def fallback(req):
        captured.append(req)
        return build_forward_body(req, adapter_kind="llama_cpp")

    body = fallback(backend)
    assert captured[0] is backend
    assert "metadata" not in body
    assert "threadwake" not in body


def test_in_process_boundary_has_no_internal_attributes():
    request = _request(
        metadata={"secret": METADATA_SECRET},
        threadwake={"enabled": True},
        codexify_identity="private",
    )
    backend = ensure_backend_chat_request(request, adapter_kind="mlx_lm")

    assert isinstance(backend, BackendChatRequest)
    assert not hasattr(backend, "metadata")
    assert not hasattr(backend, "threadwake")
    assert not hasattr(backend, "codexify_identity")
    assert backend.messages[0].content == PROMPT_SENTINEL


def test_generate_boundary_preserves_identity_without_ingress_extras():
    request = GenerateRequest(prompt=PROMPT_SENTINEL, request_id="task-123")
    backend = ensure_backend_generate_request(request, adapter_kind="stub")

    assert backend.prompt == PROMPT_SENTINEL
    assert backend.request_id == "task-123"
    assert not hasattr(backend, "metadata")


def test_original_ingress_request_is_not_mutated():
    request = _request(
        metadata={"source": METADATA_SECRET},
        threadwake={"enabled": True},
        custom_control="ignored",
    )
    original_extra = dict(request.extra_fields)
    backend = sanitize_chat_request(request, adapter_kind="stub")

    assert request.metadata == {"source": METADATA_SECRET}
    assert request.threadwake == {"enabled": True}
    assert request.extra_fields == original_extra
    assert backend is not request


def test_policy_diagnostics_have_names_and_counts_but_not_values(caplog):
    request = _request(
        metadata={"secret": METADATA_SECRET},
        threadwake={"enabled": True},
        custom_control=TOOL_SECRET,
    )
    with caplog.at_level(logging.INFO, logger="whooshd.backend_request_policy"):
        sanitize_chat_request(request, adapter_kind="stub", request_id="task-123")

    text = caplog.text
    assert "task-123" in text
    assert "stub" in text
    assert "metadata" in text
    assert "threadwake" in text
    assert "stripped_field_count=3" in text
    assert METADATA_SECRET not in text
    assert TOOL_SECRET not in text
    assert PROMPT_SENTINEL not in text


@pytest.mark.asyncio
async def test_chat_route_rejects_unknown_inference_field_without_echoing_value():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
                "unknown_temperature": METADATA_SECRET,
            },
        )

    assert response.status_code == 400
    assert response.json()["message"] == "Unsupported request field"
    assert METADATA_SECRET not in response.text
    assert PROMPT_SENTINEL not in response.text

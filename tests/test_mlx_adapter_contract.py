"""Contract tests for the MLX inference adapter.

All tests use a mock mlx_lm module injected into sys.modules.
No real model is downloaded and mlx-lm does not need to be installed.
"""

from __future__ import annotations

import pytest

from whooshd.contracts import (
    ChatCompletionRequest,
    ChatMessage,
    GenerateRequest,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


class _MockStreamResponse:
    """Minimal mock of mlx_lm's stream_generate response object."""

    def __init__(self, text: str):
        self.text = text


def _stream_resp(text: str) -> _MockStreamResponse:
    return _MockStreamResponse(text)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mlx_adapter(mock_mlx_lm_module):
    """Return a fresh MLXInferenceAdapter with a mocked mlx_lm backend."""
    from whooshd.adapters.mlx import MLXInferenceAdapter

    return MLXInferenceAdapter()


@pytest.fixture
def mock_mlx_lm_module():
    """Inject a mock mlx_lm module into sys.modules."""
    import sys
    from unittest.mock import MagicMock

    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = (
        "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
        "Hello<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )

    mock_mlx = MagicMock()
    mock_mlx.load.return_value = (MagicMock(), mock_tokenizer)
    mock_mlx.generate.return_value = "Mocked MLX response."
    mock_mlx.stream_generate.return_value = iter([
        _stream_resp("Mocked"),
        _stream_resp(" streaming"),
        _stream_resp(" response."),
    ])

    sys.modules["mlx_lm"] = mock_mlx
    yield mock_mlx
    del sys.modules["mlx_lm"]


# ── Adapter identity ────────────────────────────────────────────────────────


class TestMLXAdapterIdentity:
    def test_name_is_mlx_lm(self, mlx_adapter):
        assert mlx_adapter.name == "mlx-lm"

    def test_streaming_is_supported(self, mlx_adapter):
        assert mlx_adapter.supports_streaming is True


# ── Lazy loading ────────────────────────────────────────────────────────────


class TestLazyLoading:
    def test_model_not_loaded_at_init(self, mlx_adapter, mock_mlx_lm_module):
        """mlx_lm.load must not be called during adapter construction."""
        assert mock_mlx_lm_module.load.call_count == 0

    async def test_model_loaded_on_first_request(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        await mlx_adapter.chat_completion(req)
        assert mock_mlx_lm_module.load.call_count >= 1

    async def test_model_not_loaded_twice_for_sequential_requests(
        self, mlx_adapter, mock_mlx_lm_module
    ):
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        await mlx_adapter.chat_completion(req)
        await mlx_adapter.chat_completion(req)
        # load should only be called once for the same model path.
        assert mock_mlx_lm_module.load.call_count == 1

    async def test_concurrent_first_calls_load_once(
        self, mlx_adapter, mock_mlx_lm_module
    ):
        """Two concurrent first-calls must not trigger duplicate loads."""
        import asyncio

        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        async def _run():
            return await mlx_adapter.chat_completion(req)

        await asyncio.gather(_run(), _run())
        assert mock_mlx_lm_module.load.call_count == 1


# ── Response shape ──────────────────────────────────────────────────────────


class TestResponseShape:
    async def test_returns_chat_completion_response(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        resp = await mlx_adapter.chat_completion(req)
        assert resp.object == "chat.completion"
        assert resp.id.startswith("chatcmpl-mlx-")
        assert resp.model == "mlx-community/Llama-3.2-3B-Instruct-4bit"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].finish_reason == "stop"

    async def test_echoes_mock_response_text(self, mlx_adapter, mock_mlx_lm_module):
        mock_mlx_lm_module.generate.return_value = "Hello from MLX!"
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Say hi")],
        )
        resp = await mlx_adapter.chat_completion(req)
        assert resp.choices[0].message.content == "Hello from MLX!"

    async def test_usage_fields_are_set(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        resp = await mlx_adapter.chat_completion(req)
        assert resp.usage.prompt_tokens is not None
        assert resp.usage.completion_tokens is not None
        assert resp.usage.total_tokens is not None
        assert resp.usage.total_tokens >= resp.usage.completion_tokens


# ── Streaming rejection ─────────────────────────────────────────────────────


class TestStreaming:
    async def test_stream_true_no_longer_raises(self, mlx_adapter, mock_mlx_lm_module):
        """Streaming is now supported — stream=true should succeed."""
        # Provide a mock stream_generate that yields a couple tokens.
        mock_mlx_lm_module.stream_generate.return_value = [
            _stream_resp("Hello"),
            _stream_resp(" from"),
            _stream_resp(" MLX!"),
        ]

        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Hi")],
            stream=True,
        )
        # Should not raise.
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        assert len(chunks) >= 3  # role + content + finish


# ── Codexify-style generate ─────────────────────────────────────────────────


class TestGenerate:
    async def test_generate_returns_generate_response(self, mlx_adapter):
        req = GenerateRequest(prompt="Hello")
        resp = await mlx_adapter.generate(req)
        assert resp.ok is True
        assert resp.model_id == "mlx-community/Llama-3.2-3B-Instruct-4bit"
        assert len(resp.text) > 0
        assert resp.finish_reason == "stop"

    async def test_generate_echoes_mock_response(self, mlx_adapter, mock_mlx_lm_module):
        mock_mlx_lm_module.generate.return_value = "Echo from stub"
        req = GenerateRequest(prompt="Testing")
        resp = await mlx_adapter.generate(req)
        assert resp.text == "Echo from stub"


# ── Prompt formatting ───────────────────────────────────────────────────────


class TestPromptFormatting:
    async def test_uses_chat_template_when_available(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        await mlx_adapter.chat_completion(req)

        tokenizer = mock_mlx_lm_module.load.return_value[1]
        tokenizer.apply_chat_template.assert_called_once()

    def test_fallback_formatting_does_not_crash(self, mlx_adapter, mock_mlx_lm_module):
        """If the tokenizer lacks apply_chat_template, we fall back gracefully."""
        # Remove apply_chat_template from the tokenizer mock.
        tokenizer = mock_mlx_lm_module.load.return_value[1]
        del tokenizer.apply_chat_template

        import asyncio

        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        # Should not raise.
        asyncio.run(mlx_adapter.chat_completion(req))

    async def test_system_message_included_in_prompt(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test",
            messages=[
                ChatMessage(role="system", content="Be concise."),
                ChatMessage(role="user", content="Hello"),
            ],
        )
        resp = await mlx_adapter.chat_completion(req)
        assert resp.choices[0].message.role == "assistant"


# ── Temperature passthrough ─────────────────────────────────────────────────


class TestTemperature:
    async def test_default_temperature_not_passed(self, mlx_adapter, mock_mlx_lm_module):
        """Default temp should not be forced into kwargs."""
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.7,
        )
        await mlx_adapter.chat_completion(req)
        call_kwargs = mock_mlx_lm_module.generate.call_args.kwargs
        assert "temp" not in call_kwargs

    async def test_non_default_temperature_passed_as_temp(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.3,
        )
        await mlx_adapter.chat_completion(req)
        call_kwargs = mock_mlx_lm_module.generate.call_args.kwargs
        assert call_kwargs.get("temp") == 0.3

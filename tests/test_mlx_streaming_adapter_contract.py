"""Contract tests for the MLX streaming adapter.

All tests use a mock mlx_lm module injected into sys.modules.
No real model is downloaded and mlx-lm does not need to be installed.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from whooshd.contracts import ChatCompletionRequest, ChatMessage


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
    from whooshd.adapters.mlx import MLXInferenceAdapter

    return MLXInferenceAdapter()


@pytest.fixture
def mock_mlx_lm_module():
    """Inject a mock mlx_lm module into sys.modules with stream_generate."""
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = (
        "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
        "Hello<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    )

    mock_mlx = MagicMock()
    mock_mlx.load.return_value = (MagicMock(), mock_tokenizer)
    mock_mlx.generate.return_value = "Mocked MLX response."
    mock_mlx.stream_generate.return_value = iter(
        [_stream_resp("Hello"), _stream_resp(" from"), _stream_resp(" MLX!")]
    )

    sys.modules["mlx_lm"] = mock_mlx
    yield mock_mlx
    del sys.modules["mlx_lm"]


# ── Basic streaming shape ───────────────────────────────────────────────────


class TestStreamingShape:
    async def test_yields_multiple_chunks(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        # role + 3 content words + finish = 5
        assert len(chunks) == 5

    async def test_first_chunk_is_role_only(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        delta = chunks[0].choices[0].delta
        assert delta.role == "assistant"
        assert delta.content is None

    async def test_content_chunks_have_text(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        content_deltas = [
            c.choices[0].delta.content
            for c in chunks[1:-1]  # skip role and finish
        ]
        assert content_deltas == ["Hello", " from", " MLX!"]

    async def test_final_chunk_is_finish_marker(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        final = chunks[-1]
        delta = final.choices[0].delta
        assert delta.role is None
        assert delta.content is None
        assert final.choices[0].finish_reason == "stop"

    async def test_chunk_ids_are_consistent(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        ids = {c.id async for c in mlx_adapter.chat_completion_stream(req)}
        assert len(ids) == 1
        the_id = next(iter(ids))
        assert the_id.startswith("chatcmpl-mlx-")

    async def test_object_field_is_chunk(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        async for chunk in mlx_adapter.chat_completion_stream(req):
            assert chunk.object == "chat.completion.chunk"

    async def test_model_field_matches_config(self, mlx_adapter):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        async for chunk in mlx_adapter.chat_completion_stream(req):
            assert chunk.model == "mlx-community/Llama-3.2-3B-Instruct-4bit"


# ── Stream failure ──────────────────────────────────────────────────────────


class TestStreamFailure:
    async def test_stream_error_raises(self, mlx_adapter, mock_mlx_lm_module):
        """If stream_generate raises, the exception propagates."""
        mock_mlx_lm_module.stream_generate.side_effect = RuntimeError("MLX crash")

        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        with pytest.raises(RuntimeError, match="MLX crash"):
            async for _ in mlx_adapter.chat_completion_stream(req):
                pass

    async def test_stream_error_marks_degraded(self, mlx_adapter, mock_mlx_lm_module):
        """On stream failure, runtime status should be DEGRADED."""
        from whooshd.runtime import get_runtime

        rt = get_runtime()
        mock_mlx_lm_module.stream_generate.side_effect = RuntimeError("crash")

        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        with pytest.raises(RuntimeError):
            async for _ in mlx_adapter.chat_completion_stream(req):
                pass

        assert rt.status.value == "degraded"


# ── Streaming with empty tokens ─────────────────────────────────────────────


class TestEmptyTokens:
    async def test_empty_text_skipped(self, mlx_adapter, mock_mlx_lm_module):
        """Tokens with empty text should not produce content chunks."""
        mock_mlx_lm_module.stream_generate.return_value = iter(
            [_stream_resp("A"), _stream_resp(""), _stream_resp("B")]
        )

        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        chunks = [c async for c in mlx_adapter.chat_completion_stream(req)]
        # role + A + B + finish = 4 (empty skipped)
        assert len(chunks) == 4
        content_deltas = [
            c.choices[0].delta.content
            for c in chunks[1:-1]
        ]
        assert content_deltas == ["A", "B"]


# ── Temperature passthrough in streaming ────────────────────────────────────


class TestStreamingTemperature:
    async def test_default_temp_not_passed(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.7,
        )
        # Consume all chunks so stream_generate is definitely called.
        async for _ in mlx_adapter.chat_completion_stream(req):
            pass

        call_kwargs = mock_mlx_lm_module.stream_generate.call_args.kwargs
        assert "temp" not in call_kwargs

    async def test_non_default_temp_passed(self, mlx_adapter, mock_mlx_lm_module):
        req = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.2,
        )
        async for _ in mlx_adapter.chat_completion_stream(req):
            pass

        call_kwargs = mock_mlx_lm_module.stream_generate.call_args.kwargs
        assert call_kwargs.get("temp") == 0.2

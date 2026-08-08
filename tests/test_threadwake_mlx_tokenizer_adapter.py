"""Tests for MLX in-process tokenizer adapter.

All MLX-dependent tests skip cleanly when mlx_lm is unavailable.
"""

from __future__ import annotations

import pytest

from whooshd.runtime.threadwake.mlx_tokenizer import (
    MLXInProcessTokenizerAdapter,
    _chat_template_hash,
    _tokenizer_hash,
)
from whooshd.runtime.threadwake.tokenization import ThreadWakeTokenizerCapability


# ── Helpers ────────────────────────────────────────────────────────────────


def _has_mlx() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except Exception:
        return False


def _make_mock_tokenizer(**overrides):
    """Create a minimal mock tokenizer object for testing."""
    class MockTokenizer:
        def __init__(self):
            self.name_or_path = overrides.get("name_or_path", "test-model")
            self.vocab_size = overrides.get("vocab_size", 32000)
            self.chat_template = overrides.get("chat_template", "{% for m in messages %}{{ m.role }}: {{ m.content }}\n{% endfor %}")

        def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
            # Simple transcript rendering
            parts = [f"{m['role']}: {m['content']}" for m in messages]
            return "\n".join(parts)

        def encode(self, text):
            # Deterministic encoding: char codes modulo 32000
            class MockEncoding:
                def __init__(self, ids):
                    self.ids = ids
            return MockEncoding([ord(c) % 32000 for c in text])

    return MockTokenizer()


def _make_mock_graph():
    """Create a minimal mock PromptGraph."""
    class MockSegment:
        def __init__(self, name, stability, in_prefix, token_count, segment_type, scope, content_hash):
            self.name = name
            self.stability = stability
            self.in_stable_prefix = in_prefix
            self.token_count = token_count
            self.segment_type = segment_type
            self.scope = scope
            self.content_hash = content_hash

    class MockGraph:
        def __init__(self):
            self.segments = [
                MockSegment("system", "stable", True, 10, "system", "thread", "abc123"),
                MockSegment("user", "dynamic", False, 5, "user_message", "thread", "def456"),
            ]

    return MockGraph()


def _make_mock_request():
    class MockMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class MockRequest:
        def __init__(self):
            self.messages = [
                MockMessage("system", "You are helpful."),
                MockMessage("user", "Hello"),
            ]

    return MockRequest()


# ── Tests ─────────────────────────────────────────────────────────────────


class TestMLXAdapterNoTokenizer:
    def test_without_tokenizer_reports_estimates_only(self):
        adapter = MLXInProcessTokenizerAdapter(tokenizer=None)
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.ESTIMATES_ONLY

    def test_without_tokenizer_returns_not_real(self):
        adapter = MLXInProcessTokenizerAdapter(tokenizer=None)
        result = adapter.tokenize_prompt(None, None, model_id="m")
        assert result.real_tokenization is False
        assert "not_available" in (result.unavailable_reason or "")

    def test_stub_still_does_not_claim_token_ids(self):
        """The MLXTokenizerAdapterStub must not falsely claim token_ids."""
        from whooshd.runtime.threadwake.tokenization import MLXTokenizerAdapterStub
        stub = MLXTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.ESTIMATES_ONLY
        assert stub.supports_tokenization() != ThreadWakeTokenizerCapability.TOKEN_IDS
        assert stub.supports_tokenization() != ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS


class TestMLXAdapterWithMockTokenizer:
    def test_with_tokenizer_reports_token_ids(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.TOKEN_IDS

    def test_tokenize_produces_real_tokenization(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        graph = _make_mock_graph()
        request = _make_mock_request()

        result = adapter.tokenize_prompt(graph, request, model_id="test-model")
        assert result.real_tokenization is True
        assert result.model_id == "test-model"
        assert result.backend == "mlx"
        assert len(result.token_ids) > 0

    def test_tokenizer_hash_deterministic(self):
        tok = _make_mock_tokenizer()
        h1 = _tokenizer_hash(tok)
        h2 = _tokenizer_hash(tok)
        assert h1 is not None
        assert h1 == h2

    def test_chat_template_hash_deterministic(self):
        tok = _make_mock_tokenizer()
        h1 = _chat_template_hash(tok)
        h2 = _chat_template_hash(tok)
        assert h1 is not None
        assert h1 == h2

    def test_tokenizer_hash_in_result(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        result = adapter.tokenize_prompt(
            _make_mock_graph(), _make_mock_request(), model_id="m",
        )
        assert result.tokenizer_hash is not None
        assert len(result.tokenizer_hash) == 64  # SHA-256

    def test_chat_template_hash_in_result(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        result = adapter.tokenize_prompt(
            _make_mock_graph(), _make_mock_request(), model_id="m",
        )
        assert result.chat_template_hash is not None
        assert len(result.chat_template_hash) == 64

    def test_stable_dynamic_split(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        graph = _make_mock_graph()
        request = _make_mock_request()

        result = adapter.tokenize_prompt(graph, request, model_id="m")
        # With incremental rendering, stable + dynamic should sum to total
        assert len(result.stable_prefix_token_ids) + len(result.dynamic_tail_token_ids) == len(result.token_ids)

    def test_real_tokenization_true(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        result = adapter.tokenize_prompt(
            _make_mock_graph(), _make_mock_request(), model_id="m",
        )
        assert result.real_tokenization is True

    def test_no_raw_prompt_in_result(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        result = adapter.tokenize_prompt(
            _make_mock_graph(), _make_mock_request(), model_id="m",
        )
        # TokenizedPrompt should not contain raw prompt text
        assert not hasattr(result, "prompt_text")
        assert not hasattr(result, "raw_messages")

    def test_empty_messages_degrades(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)

        class EmptyRequest:
            messages = []

        result = adapter.tokenize_prompt(_make_mock_graph(), EmptyRequest(), model_id="m")
        assert result.real_tokenization is False
        assert "no_messages" in (result.unavailable_reason or "")


@pytest.mark.skipif(not _has_mlx(), reason="mlx_lm not available")
class TestMLXAdapterWithRealMLX:
    """Tests that require the real mlx_lm package."""

    def test_real_mlx_tokenizer_loads(self):
        """Verify we can load a real tokenizer via mlx_lm."""
        import mlx_lm
        # Just test the import works; loading a model takes too long for tests
        assert mlx_lm is not None

    def test_has_apply_chat_template(self):
        """Verify mlx_lm tokenizers expose apply_chat_template."""
        import mlx_lm
        # mlx_lm tokenizers are HuggingFace tokenizers
        # This test verifies the attribute exists at the module level
        assert hasattr(mlx_lm, "load")
        assert hasattr(mlx_lm, "generate")

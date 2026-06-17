"""Fidelity tests for MLX tokenizer adapter.

Proves that the adapter uses the tokenizer object correctly and that
tokenization degrades safely.  MLX-dependent tests skip cleanly.
"""

from __future__ import annotations

import pytest

from whooshd.runtime.threadwake.mlx_tokenizer import (
    MLXInProcessTokenizerAdapter,
    _render_prompt,
    _tokenize,
)


def _has_mlx() -> bool:
    try:
        import mlx_lm  # noqa: F401
        return True
    except ImportError:
        return False


def _make_mock_tokenizer(**overrides):
    class MockTokenizer:
        def __init__(self):
            self.name_or_path = overrides.get("name_or_path", "test-model")
            self.vocab_size = overrides.get("vocab_size", 32000)
            self.chat_template = overrides.get(
                "chat_template",
                "{% for m in messages %}{{ m.role }}: {{ m.content }}\n{% endfor %}",
            )

        def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
            parts = [f"{m['role']}: {m['content']}" for m in messages]
            return "\n".join(parts)

        def encode(self, text):
            class MockEncoding:
                def __init__(self, ids):
                    self.ids = ids
            return MockEncoding([ord(c) % 32000 for c in text])

    return MockTokenizer()


class TestFidelity:
    def test_tokenization_is_deterministic(self):
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)

        class MockSegment:
            def __init__(self, name, stability, in_prefix, token_count, seg_type, scope, ch):
                self.name = name
                self.stability = stability
                self.in_stable_prefix = in_prefix
                self.token_count = token_count
                self.segment_type = seg_type
                self.scope = scope
                self.content_hash = ch

        class MockGraph:
            segments = [
                MockSegment("s", "stable", True, 5, "system", "thread", "abc"),
                MockSegment("u", "dynamic", False, 3, "user_message", "thread", "def"),
            ]

        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockReq:
            messages = [MockMsg("system", "Hi"), MockMsg("user", "Hello")]

        r1 = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        r2 = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        assert r1.token_ids == r2.token_ids
        assert r1.stable_prefix_token_ids == r2.stable_prefix_token_ids

    def test_tokenizer_error_degrades_safely(self):
        """A tokenizer that raises should produce real_tokenization=False."""
        class BrokenTokenizer:
            name_or_path = "broken"
            vocab_size = 100

            def apply_chat_template(self, messages, **kwargs):
                raise RuntimeError("tokenizer crash")

        adapter = MLXInProcessTokenizerAdapter(tokenizer=BrokenTokenizer())

        class MockSeg:
            name = "s"; stability = "stable"; in_stable_prefix = True
            token_count = 5; segment_type = "system"; scope = "thread"; content_hash = "abc"

        class MockGraph:
            segments = [MockSeg()]

        class MockMsg:
            role = "user"; content = "hi"

        class MockReq:
            messages = [MockMsg()]

        result = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        assert result.real_tokenization is False
        assert "failed" in (result.unavailable_reason or "").lower()

    def test_render_prompt_fallback_works(self):
        """Fallback rendering without chat_template should still produce text."""
        class NoTemplateTokenizer:
            name_or_path = "no-template"
            vocab_size = 1000

            def encode(self, text):
                class E:
                    ids = [1, 2, 3]
                return E()

        tok = NoTemplateTokenizer()
        result = _render_prompt(tok, [{"role": "user", "content": "hello"}])
        assert result is not None
        assert "hello" in result

    def test_tokenize_with_encoding_object(self):
        """Tokenizer returning an Encoding object should work."""
        class EncodingTokenizer:
            name_or_path = "enc"
            vocab_size = 2000
            chat_template = "tmpl"

            def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
                return "rendered"

            def encode(self, text):
                class Encoding:
                    ids = [10, 20, 30]
                return Encoding()

        adapter = MLXInProcessTokenizerAdapter(tokenizer=EncodingTokenizer())

        class MockSeg:
            name = "s"; stability = "stable"; in_stable_prefix = True
            token_count = 5; segment_type = "system"; scope = "thread"; content_hash = "abc"

        class MockGraph:
            segments = [MockSeg()]

        class MockMsg:
            role = "user"; content = "hi"

        class MockReq:
            messages = [MockMsg()]

        result = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        assert result.real_tokenization is True
        assert result.token_ids == [10, 20, 30]

    def test_no_chat_template_degrades_cleanly(self):
        """Tokenizer without chat_template should still tokenize via fallback."""
        class NoTemplateTok:
            name_or_path = "no-tmpl"
            vocab_size = 500
            chat_template = None  # No template

            def encode(self, text):
                class E:
                    ids = [1, 2]
                return E()

        adapter = MLXInProcessTokenizerAdapter(tokenizer=NoTemplateTok())

        class MockSeg:
            name = "s"; stability = "stable"; in_stable_prefix = True
            token_count = 5; segment_type = "system"; scope = "thread"; content_hash = "abc"

        class MockGraph:
            segments = [MockSeg()]

        class MockMsg:
            role = "user"; content = "hi"

        class MockReq:
            messages = [MockMsg()]

        result = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        # Should still work via fallback rendering
        assert result.real_tokenization is True
        assert result.chat_template_hash is None

    def test_capability_is_token_ids_not_spans(self):
        """MLX adapter should report token_ids, not token_ids_with_spans."""
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)
        cap = adapter.supports_tokenization()
        assert cap.value == "token_ids"
        assert cap.value != "token_ids_with_spans"

    def test_stable_dynamic_split_preserves_total(self):
        """stable_prefix + dynamic_tail should equal total tokens."""
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)

        class MockSeg:
            def __init__(self, name, stability, in_prefix, token_count, seg_type, scope, ch):
                self.name = name
                self.stability = stability
                self.in_stable_prefix = in_prefix
                self.token_count = token_count
                self.segment_type = seg_type
                self.scope = scope
                self.content_hash = ch

        class MockGraph:
            segments = [
                MockSeg("system", "stable", True, 5, "system", "thread", "abc"),
                MockSeg("user", "dynamic", False, 3, "user_message", "thread", "def"),
            ]

        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockReq:
            messages = [
                MockMsg("system", "System prompt here"),
                MockMsg("user", "User query here"),
            ]

        r = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        assert len(r.stable_prefix_token_ids) + len(r.dynamic_tail_token_ids) == len(r.token_ids)
        assert r.stable_prefix_token_count == len(r.stable_prefix_token_ids)
        assert r.dynamic_tail_token_count == len(r.dynamic_tail_token_ids)

    def test_spans_are_observability_only(self):
        """Spans should be present but without exact start/end positions (token_ids only)."""
        tok = _make_mock_tokenizer()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tok)

        class MockSeg:
            def __init__(self, name, stability, in_prefix, token_count, seg_type, scope, ch):
                self.name = name
                self.stability = stability
                self.in_stable_prefix = in_prefix
                self.token_count = token_count
                self.segment_type = seg_type
                self.scope = scope
                self.content_hash = ch

        class MockGraph:
            segments = [
                MockSeg("system", "stable", True, 5, "system", "thread", "abc123"),
                MockSeg("user", "dynamic", False, 3, "user_message", "thread", "def456"),
            ]

        class MockMsg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        class MockReq:
            messages = [
                MockMsg("system", "s"),
                MockMsg("user", "u"),
            ]

        r = adapter.tokenize_prompt(MockGraph(), MockReq(), model_id="m")
        assert len(r.spans) == 2
        assert r.spans[0].segment_name == "system"
        assert r.spans[1].segment_name == "user"


@pytest.mark.skipif(not _has_mlx(), reason="mlx_lm not available")
class TestMLXFidelityRealMLX:
    def test_real_mlx_imports(self):
        import mlx_lm
        assert mlx_lm is not None

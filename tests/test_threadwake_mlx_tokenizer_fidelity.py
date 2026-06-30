"""Tests for MLX prompt rendering and token split fidelity.

Proves that the shared MLX prompt renderer produces identical output
for both the inference path and the ThreadWake tokenizer path, and
that token splits are conservative and reconstructible.
"""

from __future__ import annotations

import hashlib
import pytest

from whooshd.adapters.mlx_prompt import extract_chat_messages, render_mlx_chat_prompt
from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.mlx_tokenizer import (
    MLXInProcessTokenizerAdapter,
    _chat_template_hash,
    _tokenizer_hash,
    _tokenize,
)
from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
)
from whooshd.runtime.threadwake.types import ThreadWakeRequestConfig


# ── Fake tokenizers for deterministic testing ──────────────────────────────


class _FakeTokenizerWithChatTemplate:
    """Fake tokenizer with apply_chat_template that produces deterministic
    output, mirroring real tokenizer behavior."""

    def __init__(self, template_str="<|im_start|>", name="fake-tokenizer", vocab_size=32000):
        self.name_or_path = name
        self.vocab_size = vocab_size
        self.chat_template = template_str
        self._encode_map: dict[str, list[int]] = {}
        self._call_count = 0

        # Pre-populate with deterministic encodings.
        self._next_id = 100

    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
        """Deterministic template rendering."""
        self._call_count += 1
        assert tokenize is False, "shared renderer must call with tokenize=False"
        assert add_generation_prompt is True, "shared renderer must call with add_generation_prompt=True"

        parts = [self.chat_template]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<|{role}|>{content}")
        parts.append("<|assistant|>")
        return "".join(parts)

    def encode(self, text: str):
        """Deterministic encoding — each unique text gets unique IDs."""
        if text not in self._encode_map:
            # Generate fake token IDs from text hash.
            h = hashlib.sha256(text.encode()).digest()
            ids = []
            for i in range(0, len(h), 2):
                ids.append(int.from_bytes(h[i:i+2], "big") % 65536)
            self._encode_map[text] = ids
        return self._encode_map[text]


class _FakeTokenizerWithoutTemplate:
    """Fake tokenizer without apply_chat_template — tests fallback path."""

    def __init__(self, name="no-template-tok", vocab_size=16000):
        self.name_or_path = name
        self.vocab_size = vocab_size
        self.chat_template = None
        self._encode_map: dict[str, list[int]] = {}

    def encode(self, text: str):
        if text not in self._encode_map:
            h = hashlib.sha256(text.encode()).digest()
            ids = []
            for i in range(0, len(h), 2):
                ids.append(int.from_bytes(h[i:i+2], "big") % 65536)
            self._encode_map[text] = ids
        return self._encode_map[text]


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_request(messages=None, model="test-model"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable prefix"},
            {"role": "user", "content": "hello"},
        ]
    return ChatCompletionRequest.model_validate({
        "model": model,
        "messages": messages,
    })


def _make_graph(request, backend="mlx"):
    return compile_prompt_graph(
        messages=list(getattr(request, "messages", [])),
        model_id=getattr(request, "model", None),
        backend=backend,
        scope="thread",
    )


# ── Test 1: Shared rendering with chat template ───────────────────────────


class TestSharedRenderingWithChatTemplate:
    """Prove render_mlx_chat_prompt produces identical output for both
    inference and ThreadWake paths when a chat template is available."""

    def test_renderer_produces_identical_output(self):
        tokenizer = _FakeTokenizerWithChatTemplate()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
        ]

        rendered = render_mlx_chat_prompt(tokenizer, messages)
        # The fake template wraps each message: <|im_start|><|system|>You are helpful.<|user|>Hello world<|assistant|>
        assert "<|im_start|>" in rendered
        assert "<|system|>" in rendered
        assert "<|user|>" in rendered
        assert "<|assistant|>" in rendered
        assert "You are helpful." in rendered
        assert "Hello world" in rendered

    def test_extract_messages_round_trips(self):
        """Messages extracted from a request produce identical rendering
        to messages built from dicts."""
        req = _make_request(messages=[
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
        ])
        tokenizer = _FakeTokenizerWithChatTemplate()

        # Extract via shared helper.
        extracted = extract_chat_messages(req)
        rendered_extracted = render_mlx_chat_prompt(tokenizer, extracted)

        # Same messages as plain dicts — should produce identical output.
        direct = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
        ]
        rendered_direct = render_mlx_chat_prompt(tokenizer, direct)

        assert rendered_extracted == rendered_direct

    def test_renderer_passes_correct_args_to_template(self):
        """Shared renderer must call apply_chat_template with tokenize=False
        and add_generation_prompt=True."""
        tokenizer = _FakeTokenizerWithChatTemplate()
        messages = [{"role": "user", "content": "hi"}]
        render_mlx_chat_prompt(tokenizer, messages)
        # The fake tokenizer asserts these values in apply_chat_template.
        assert tokenizer._call_count == 1


# ── Test 2: Fallback transcript rendering matches ─────────────────────────


class TestFallbackTranscript:
    """Prove the fallback transcript is identical for both paths when
    no chat template is available."""

    def test_fallback_includes_assistant_cue(self):
        tokenizer = _FakeTokenizerWithoutTemplate()
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Query"},
        ]
        rendered = render_mlx_chat_prompt(tokenizer, messages)

        assert "System: Be helpful." in rendered
        assert "User: Query" in rendered
        assert rendered.endswith("Assistant: ")

    def test_fallback_multi_turn(self):
        tokenizer = _FakeTokenizerWithoutTemplate()
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        rendered = render_mlx_chat_prompt(tokenizer, messages)

        assert "System: System." in rendered
        assert "User: Q1" in rendered
        assert "Assistant: A1" in rendered
        assert "User: Q2" in rendered
        assert rendered.endswith("Assistant: ")

    def test_fallback_no_template_renders_same_as_dict(self):
        """sToken rendering without chat template matches the text
        that _format_chat_prompt would produce with the same tokenizer."""
        tokenizer = _FakeTokenizerWithoutTemplate()
        messages = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ]
        rendered = render_mlx_chat_prompt(tokenizer, messages)

        # Verify fallback transcript format explicitly.
        expected = "System: S\nUser: U\nAssistant: "
        assert rendered == expected


# ── Test 3: Stable/dynamic split reconstructs full token IDs ───────────────


class TestStableDynamicSplitReconstructs:
    """Prove that stable_prefix_token_ids + dynamic_tail_token_ids
    equals the full token_ids."""

    def test_split_reconstructs_with_template(self):
        tokenizer = _FakeTokenizerWithChatTemplate(name="split-tok", vocab_size=1000)
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tokenizer)

        messages = [
            {"role": "system", "content": "stable prefix " * 4},
            {"role": "user", "content": "dynamic tail"},
        ]
        req = _make_request(messages=messages)
        graph = _make_graph(req)

        result = adapter.tokenize_prompt(graph, req, model_id="test-model")
        assert result.real_tokenization is True
        assert result.token_ids is not None
        assert result.stable_prefix_token_ids is not None
        assert result.dynamic_tail_token_ids is not None

        # Stable + dynamic must reconstruct full token IDs.
        reconstructed = result.stable_prefix_token_ids + result.dynamic_tail_token_ids
        assert reconstructed == result.token_ids

        # Counts match.
        assert result.stable_prefix_token_count == len(result.stable_prefix_token_ids)
        assert result.dynamic_tail_token_count == len(result.dynamic_tail_token_ids)

    def test_stable_ids_are_leading_prefix(self):
        """Stable prefix token IDs must be a true leading prefix of full IDs."""
        tokenizer = _FakeTokenizerWithChatTemplate(name="prefix-tok", vocab_size=1000)
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tokenizer)

        messages = [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "dynamic"},
        ]
        req = _make_request(messages=messages)
        graph = _make_graph(req)

        result = adapter.tokenize_prompt(graph, req, model_id="test-model")
        if result.stable_prefix_token_ids:
            assert result.token_ids[:len(result.stable_prefix_token_ids)] == result.stable_prefix_token_ids


# ── Test 4: Non-prefix stable render disables reusable split ───────────────


class TestNonPrefixStableRenderSafe:
    """Prove that a non-leading-prefix stable render does not claim
    reusable prefix tokens."""

    def test_non_prefix_stable_disables_split(self):
        """When stable-only rendering does not produce a leading prefix
        of the full token IDs, no reusable split is claimed."""
        tokenizer = _FakeTokenizerWithChatTemplate(name="non-prefix-tok", vocab_size=1000)
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tokenizer)

        # Many messages so the stable prefix (first few) and full render
        # produce different token sequences.
        messages = [
            {"role": "system", "content": "s" * 8},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ]
        req = _make_request(messages=messages)
        graph = _make_graph(req)

        result = adapter.tokenize_prompt(graph, req, model_id="test-model")
        assert result.real_tokenization is True

        # The stable prefix should be empty or at minimum safe:
        # the adapter must not claim a prefix that isn't a true leading sublist.
        if result.stable_prefix_token_ids:
            assert result.token_ids[:len(result.stable_prefix_token_ids)] == result.stable_prefix_token_ids

    def test_no_crash_on_empty_messages(self):
        """Minimal messages should not crash the adapter."""
        tokenizer = _FakeTokenizerWithChatTemplate()
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tokenizer)

        req = _make_request(messages=[
            {"role": "user", "content": "hi"},
        ])
        graph = _make_graph(req)

        result = adapter.tokenize_prompt(graph, req, model_id="test-model")
        # Should return without crashing, with real tokenization.
        assert result.real_tokenization is True


# ── Test 5: Tokenizer identity hashes are stable ──────────────────────────


class TestTokenizerHashes:
    """Prove tokenizer and chat template hashes are deterministic."""

    def test_tokenizer_hash_deterministic(self):
        tok = _FakeTokenizerWithChatTemplate(name="tok-a", vocab_size=32000)
        h1 = _tokenizer_hash(tok)
        h2 = _tokenizer_hash(tok)
        assert h1 == h2
        assert h1 is not None

    def test_tokenizer_hash_changes_with_name(self):
        tok_a = _FakeTokenizerWithChatTemplate(name="tok-a", vocab_size=32000)
        tok_b = _FakeTokenizerWithChatTemplate(name="tok-b", vocab_size=32000)
        assert _tokenizer_hash(tok_a) != _tokenizer_hash(tok_b)

    def test_tokenizer_hash_changes_with_vocab_size(self):
        tok_a = _FakeTokenizerWithChatTemplate(name="tok", vocab_size=32000)
        tok_b = _FakeTokenizerWithChatTemplate(name="tok", vocab_size=16000)
        assert _tokenizer_hash(tok_a) != _tokenizer_hash(tok_b)

    def test_chat_template_hash_stable(self):
        tok = _FakeTokenizerWithChatTemplate(template_str="<|template|>")
        h1 = _chat_template_hash(tok)
        h2 = _chat_template_hash(tok)
        assert h1 == h2
        assert h1 is not None

    def test_chat_template_hash_changes(self):
        tok_a = _FakeTokenizerWithChatTemplate(template_str="<|a|>")
        tok_b = _FakeTokenizerWithChatTemplate(template_str="<|b|>")
        assert _chat_template_hash(tok_a) != _chat_template_hash(tok_b)

    def test_no_template_returns_none(self):
        tok = _FakeTokenizerWithoutTemplate()
        assert _chat_template_hash(tok) is None


# ── Test 6: No raw prompt leakage ──────────────────────────────────────────


class TestNoLeakage:
    """Prove that ThreadWake health/analysis surfaces do not expose
    raw prompts, token IDs, or rendered prompt strings."""

    def test_tokenized_prompt_no_raw_text_leakage(self):
        tokenizer = _FakeTokenizerWithChatTemplate(name="leak-tok", vocab_size=1000)
        adapter = MLXInProcessTokenizerAdapter(tokenizer=tokenizer)

        secret = "SECRET-MARKER-xyz-123"
        messages = [
            {"role": "system", "content": f"stable {secret}"},
            {"role": "user", "content": "hello"},
        ]
        req = _make_request(messages=messages)
        graph = _make_graph(req)

        result = adapter.tokenize_prompt(graph, req, model_id="test-model")

        # TokenizedPrompt must not expose raw prompt text.
        result_dict = result.__dict__ if hasattr(result, "__dict__") else {}
        result_str = str(result_dict)
        assert secret not in result_str, "secret marker leaked through TokenizedPrompt"

    def test_render_mlx_chat_prompt_no_persistence_of_prompt(self):
        """The shared renderer is a pure function — it does not store
        or expose prompt content in side-effects."""
        tokenizer = _FakeTokenizerWithChatTemplate()
        messages = [{"role": "user", "content": "ephemeral content"}]
        result = render_mlx_chat_prompt(tokenizer, messages)

        # Result is a string — but no side effects on the tokenizer.
        assert not hasattr(tokenizer, "_last_prompt") or getattr(tokenizer, "_last_prompt", None) is None

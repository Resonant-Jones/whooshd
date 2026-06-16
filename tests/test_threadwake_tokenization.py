"""Tests for ThreadWake tokenization module — adapters, spans, capability."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
    NoOpTokenizerAdapter,
    ThreadWakeTokenizerCapability,
    TokenizedPrompt,
    TokenSpan,
)


# ── NoOpTokenizerAdapter ──────────────────────────────────────────────────


class TestNoOpTokenizerAdapter:
    def test_reports_unsupported(self):
        adapter = NoOpTokenizerAdapter()
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_tokenize_prompt_returns_unavailable(self):
        adapter = NoOpTokenizerAdapter()
        result = adapter.tokenize_prompt(None, None, model_id="m")
        assert result.real_tokenization is False
        assert result.unavailable_reason == "backend_tokenizer_unsupported"
        assert result.token_ids == []


# ── FakeTokenizerAdapter ──────────────────────────────────────────────────


class TestFakeTokenizerAdapter:
    def test_reports_token_ids_with_spans(self):
        adapter = FakeTokenizerAdapter()
        assert adapter.supports_tokenization() == ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS

    def test_produces_real_tokenization(self):
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
        })
        graph = compile_prompt_graph(
            model_id="m", backend="fake",
            messages=list(req.messages),
        )
        adapter = FakeTokenizerAdapter()
        result = adapter.tokenize_prompt(graph, req, model_id="m")

        assert result.real_tokenization is True
        assert result.unavailable_reason is None
        assert len(result.token_ids) > 0
        assert result.model_id == "m"
        assert result.backend == "fake"

    def test_stable_prefix_ids_exclude_dynamic_segments(self):
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
        })
        graph = compile_prompt_graph(model_id="m", backend="fake", messages=list(req.messages))
        adapter = FakeTokenizerAdapter()
        result = adapter.tokenize_prompt(graph, req, model_id="m")

        # System message is stable, user is dynamic (latest user)
        assert len(result.stable_prefix_token_ids) > 0
        assert len(result.dynamic_tail_token_ids) > 0
        # Stable + dynamic should sum to total
        assert len(result.stable_prefix_token_ids) + len(result.dynamic_tail_token_ids) == len(result.token_ids)

    def test_spans_align_to_segments(self):
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
        })
        graph = compile_prompt_graph(model_id="m", backend="fake", messages=list(req.messages))
        adapter = FakeTokenizerAdapter()
        result = adapter.tokenize_prompt(graph, req, model_id="m")

        assert len(result.spans) == len(graph.segments)
        for i, span in enumerate(result.spans):
            assert span.segment_index == i
            assert span.segment_name == graph.segments[i].name
            assert span.content_hash == graph.segments[i].content_hash

    def test_token_ids_are_deterministic(self):
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
        })
        graph = compile_prompt_graph(model_id="m", backend="fake", messages=list(req.messages))
        adapter = FakeTokenizerAdapter()

        r1 = adapter.tokenize_prompt(graph, req, model_id="m")
        r2 = adapter.tokenize_prompt(graph, req, model_id="m")
        assert r1.token_ids == r2.token_ids
        assert r1.stable_prefix_token_ids == r2.stable_prefix_token_ids


# ── Registry ──────────────────────────────────────────────────────────────


class TestTokenizerRegistry:
    def test_unregistered_backend_returns_noop(self):
        registry = BackendTokenizerAdapterRegistry()
        adapter = registry.get("nonexistent")
        assert isinstance(adapter, NoOpTokenizerAdapter)

    def test_unsupported_backend_reports_unsupported(self):
        registry = BackendTokenizerAdapterRegistry()
        assert registry.capability("nonexistent") == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_has_real_tokenization_false_for_unsupported(self):
        registry = BackendTokenizerAdapterRegistry()
        assert registry.has_real_tokenization("nonexistent") is False

    def test_register_and_retrieve(self):
        registry = BackendTokenizerAdapterRegistry()
        registry.register("fake", FakeTokenizerAdapter())
        assert registry.has_real_tokenization("fake") is True
        assert registry.capability("fake") == ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS

    def test_registered_backends_lists_keys(self):
        registry = BackendTokenizerAdapterRegistry()
        registry.register("a", FakeTokenizerAdapter())
        registry.register("b", FakeTokenizerAdapter())
        assert set(registry.registered_backends()) == {"a", "b"}

    def test_clear_removes_all(self):
        registry = BackendTokenizerAdapterRegistry()
        registry.register("a", FakeTokenizerAdapter())
        registry.clear()
        assert registry.registered_backends() == []
        assert isinstance(registry.get("a"), NoOpTokenizerAdapter)

"""Tests for Codexify metadata validation — hash mismatches, invalid indices, scope rejection."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.keys import canonicalize_content, sha256_hex
from whooshd.runtime.threadwake.metadata import (
    MetadataValidationError,
    parse_codexify_segments,
    validate_and_merge_segments,
)
from whooshd.runtime.threadwake.types import PromptSegment


def _make_inferred_segments(count=2):
    return [
        PromptSegment(
            name=f"msg:{i}:user", role="user", content_hash=sha256_hex(f"content_{i}"),
            segment_type="message", stability="dynamic", token_count=5,
        )
        for i in range(count)
    ]


# ── Content hash validation ───────────────────────────────────────────────


class TestContentHashValidation:
    def test_matching_hash_is_accepted(self):
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
        ]
        # Hash only the content, not the full message payload
        canonical = canonicalize_content("System prompt")
        content_hash = sha256_hex(canonical)

        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable", "content_hash": content_hash},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": messages, "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub", messages=messages, codexify_segments=segments,
        )
        # Should not raise; valid hash = accepted
        assert graph.segments[0].name == "guardian"

    def test_mismatched_hash_degrades_to_inferred(self):
        messages = [
            {"role": "system", "content": "Actual system prompt"},
        ]
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable", "content_hash": "0000000000000000000000000000000000000000000000000000000000000000"},
        ]
        graph = compile_prompt_graph(
            model_id="m", backend="stub", messages=messages, codexify_segments=segments,
        )
        # Hash mismatch → metadata ignored, falls back to inferred
        assert graph.segments[0].name != "guardian"  # Should be inferred name

    def test_retrieval_stable_requires_hash_validation(self):
        """Retrieval marked as 'stable' without content_hash → degraded to semi_stable."""
        segments = [
            {"name": "retrieval", "message_index": 0, "segment_type": "retrieval", "stability": "stable", "content_hash": None},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "system", "content": "Context"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].stability == "semi_stable"


# ── Invalid message_index ─────────────────────────────────────────────────


class TestInvalidMessageIndex:
    def test_negative_message_index_degrades(self):
        segments = [
            {"name": "bad", "message_index": -1, "segment_type": "system", "stability": "stable"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "user", "content": "Hello"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        # Should not crash; segment should fall back to inferred
        assert len(graph.segments) >= 1

    def test_out_of_range_message_index_degrades(self):
        segments = [
            {"name": "bad", "message_index": 99, "segment_type": "system", "stability": "stable"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "user", "content": "Hello"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert len(graph.segments) == 1  # Still one segment (inferred)


# ── Global scope rejection ────────────────────────────────────────────────


class TestGlobalScopeRejection:
    def test_global_scope_rejected_by_default(self):
        segments = [
            {"name": "global_seg", "message_index": 0, "segment_type": "system", "stability": "stable", "scope": "global"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "system", "content": "Prompt"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
            allow_global=False,
        )
        # Global scope rejected → falls back to inferred (default scope = thread)
        assert graph.segments[0].scope == "thread"

    def test_global_scope_allowed_when_enabled(self):
        segments = [
            {"name": "global_seg", "message_index": 0, "segment_type": "system", "stability": "stable", "scope": "global"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "system", "content": "Prompt"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
            allow_global=True,
        )
        assert graph.segments[0].scope == "global"


# ── Default stability rules ───────────────────────────────────────────────


class TestDefaultStabilityRules:
    def test_tool_output_defaults_dynamic(self):
        segments = [
            {"name": "tool_result", "message_index": 0, "segment_type": "tool_output"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "tool", "content": "Result"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].stability == "dynamic"

    def test_retrieval_defaults_semi_stable(self):
        segments = [
            {"name": "retrieval", "message_index": 0, "segment_type": "retrieval"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "system", "content": "Retrieved content"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].stability == "semi_stable"

    def test_unknown_defaults_dynamic(self):
        segments = [
            {"name": "mystery", "message_index": 0, "segment_type": "unknown"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "user", "content": "?"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].stability == "dynamic"


# ── Parse / edge cases ────────────────────────────────────────────────────


class TestParseMetadata:
    def test_parse_none_returns_none(self):
        assert parse_codexify_segments(None) is None

    def test_parse_empty_list_returns_empty_metadata(self):
        result = parse_codexify_segments([])
        assert result is not None
        assert result.segments == []

    def test_parse_invalid_returns_none(self):
        result = parse_codexify_segments("not-valid")
        assert result is None

    def test_parse_valid_segments(self):
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
        ]
        result = parse_codexify_segments(segments)
        assert result is not None
        assert len(result.segments) == 1
        assert result.segments[0].name == "guardian"

    def test_duplicate_message_index_degrades_second(self):
        segments = [
            {"name": "first", "message_index": 0, "segment_type": "system", "stability": "stable"},
            {"name": "duplicate", "message_index": 0, "segment_type": "persona", "stability": "stable"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [{"role": "system", "content": "Prompt"}],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        # First segment should use first metadata entry; duplicate is dropped
        assert graph.segments[0].name == "first"


# ── Cacheable flag ───────────────────────────────────────────────────────


class TestCacheableFlag:
    def test_cacheable_false_forces_dynamic(self):
        segments = [
            {"name": "secret", "message_index": 0, "segment_type": "system", "stability": "stable", "cacheable": False},
            {"name": "user", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = ChatCompletionRequest.model_validate({
            "model": "m", "messages": [
                {"role": "system", "content": "Secret prompt"},
                {"role": "user", "content": "Hello"},
            ],
            "threadwake_segments": segments,
        })
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        # cacheable=false → forced to dynamic → not in stable prefix
        assert graph.segments[0].stability == "dynamic"
        assert graph.segments[0].in_stable_prefix is False

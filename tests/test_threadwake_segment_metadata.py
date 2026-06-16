"""Tests for Codexify segment metadata — override, validation, and fallback."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


def _request(messages=None, threadwake_segments=None, thread_id=None, threadwake_config=None):
    if messages is None:
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
    data = {"model": "test-model", "messages": messages}
    if threadwake_segments is not None:
        data["threadwake_segments"] = threadwake_segments
    if thread_id:
        data["thread_id"] = thread_id
    if threadwake_config:
        data["threadwake"] = threadwake_config
    return ChatCompletionRequest.model_validate(data)


def _make_mgr():
    return ThreadWakeManager(metrics=ThreadWakeMetrics())


# ── Valid metadata overrides inferred segment type ─────────────────────────


class TestValidMetadataOverride:
    def test_metadata_overrides_segment_type(self):
        """Codexify metadata should set the segment_type to the provided value."""
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
            {"name": "latest_user", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )

        assert graph.segments[0].segment_type == "system"
        assert graph.segments[0].name == "guardian"
        assert graph.segments[1].segment_type == "user_message"
        assert graph.segments[1].name == "latest_user"

    def test_metadata_stability_overrides_inferred(self):
        """Explicit stability in metadata should override inferred stability."""
        segments = [
            {"name": "retrieval_bundle", "message_index": 0, "segment_type": "retrieval", "stability": "stable", "content_hash": None},
            {"name": "user_msg", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(
            messages=[
                {"role": "system", "content": "Retrieved context content here..."},
                {"role": "user", "content": "Hello"},
            ],
            threadwake_segments=segments,
        )
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        # Retrieval without content_hash → defaults to semi_stable even if stable
        assert graph.segments[0].stability == "semi_stable"

    def test_persona_segment_is_stable(self):
        segments = [
            {"name": "persona_layer", "message_index": 0, "segment_type": "persona", "stability": "stable"},
            {"name": "user_msg", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].segment_type == "persona"
        assert graph.segments[0].stability == "stable"
        assert graph.segments[0].in_stable_prefix is True

    def test_tools_segment_is_stable(self):
        segments = [
            {"name": "tool_manifest", "message_index": 0, "segment_type": "tools", "stability": "stable"},
            {"name": "user_msg", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].segment_type == "tool_schema"
        assert graph.segments[0].in_stable_prefix is True


# ── Dynamic segments excluded from stable prefix ───────────────────────────


class TestDynamicExclusion:
    def test_user_segment_is_always_dynamic(self):
        segments = [
            {"name": "system", "message_index": 0, "segment_type": "system", "stability": "stable"},
            {"name": "user", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[1].stability == "dynamic"
        assert graph.segments[1].in_stable_prefix is False

    def test_tool_output_is_always_dynamic(self):
        segments = [
            {"name": "system", "message_index": 0, "segment_type": "system", "stability": "stable"},
            {"name": "tool_result", "message_index": 1, "segment_type": "tool_output", "stability": "stable"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        # tool_output is always dynamic regardless of requested stability
        assert graph.segments[1].segment_type == "tool_output"
        assert graph.segments[1].stability == "dynamic"

    def test_dynamic_segment_not_in_stable_prefix(self):
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
            {"name": "latest_turn", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].in_stable_prefix is True
        assert graph.segments[1].in_stable_prefix is False


# ── No metadata uses inferred behavior ────────────────────────────────────


class TestMissingMetadata:
    def test_no_metadata_uses_inferred_behavior(self):
        req = _request(threadwake_segments=None)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
        )
        # system → "message" (inferred), user → "message" (inferred)
        assert graph.segments[0].segment_type == "message"
        assert graph.segments[0].stability == "stable"  # system role inferred as stable
        assert graph.segments[1].stability == "dynamic"  # latest user is dynamic

    def test_empty_metadata_uses_inferred(self):
        req = _request(threadwake_segments=[])
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=[],
        )
        assert graph.segments[0].segment_type == "message"

    def test_metadata_for_subset_of_segments(self):
        """Only some segments have metadata — rest are inferred."""
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
            # No metadata for index 1
        ]
        req = _request(threadwake_segments=segments)
        graph = compile_prompt_graph(
            model_id=req.model, backend="stub",
            messages=list(req.messages),
            codexify_segments=segments,
        )
        assert graph.segments[0].name == "guardian"
        # Segment 1 should still exist with inferred values
        assert graph.segments[1].segment_type == "message"


# ── No raw prompt leakage ─────────────────────────────────────────────────


class TestNoRawPromptLeakage:
    def test_metadata_does_not_contain_raw_content(self):
        segments = [
            {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
        ]
        req = _request(
            messages=[{"role": "system", "content": "SECRET_SYSTEM_PROMPT_DO_NOT_LEAK"}],
            threadwake_segments=segments,
        )
        observation = _make_mgr().observe_request(req, backend="stub")
        dumped = observation.model_dump_json()
        assert "SECRET_SYSTEM_PROMPT_DO_NOT_LEAK" not in dumped

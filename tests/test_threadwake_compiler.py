from __future__ import annotations

from whooshd.runtime.threadwake.compiler import compile_prompt_graph


def test_system_user_prompt_generates_stable_prefix_hash():
    graph = compile_prompt_graph(
        model_id="stub-model",
        backend="stub",
        messages=[
            {"role": "system", "content": "You are local-first."},
            {"role": "user", "content": "Hello"},
        ],
    )

    assert len(graph.stable_prefix_hash) == 64
    assert graph.stable_prefix_tokens > 0
    assert graph.dynamic_tokens > 0


def test_repeated_same_prompt_generates_same_hash():
    messages = [
        {"role": "system", "content": "Stable instruction\r\nline two"},
        {"role": "user", "content": "What now?"},
    ]

    first = compile_prompt_graph(model_id="m", backend="stub", messages=messages)
    second = compile_prompt_graph(model_id="m", backend="stub", messages=messages)

    assert second.stable_prefix_hash == first.stable_prefix_hash
    assert second.full_prompt_hash == first.full_prompt_hash


def test_changed_system_prompt_changes_stable_prefix_hash():
    first = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable instruction A"},
            {"role": "user", "content": "Hello"},
        ],
    )
    second = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable instruction B"},
            {"role": "user", "content": "Hello"},
        ],
    )

    assert second.stable_prefix_hash != first.stable_prefix_hash


def test_latest_user_message_is_dynamic():
    graph = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Earlier"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Latest"},
        ],
    )

    latest = graph.segments[-1]
    assert latest.role == "user"
    assert latest.stability == "dynamic"
    assert latest.in_stable_prefix is False


def test_tool_schema_is_stable_prefix_segment():
    graph = compile_prompt_graph(
        model_id="m",
        backend="stub",
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        messages=[{"role": "user", "content": "Latest"}],
    )

    assert graph.segments[0].segment_type == "tool_schema"
    assert graph.segments[0].stability == "stable"
    assert graph.segments[0].in_stable_prefix is True

from __future__ import annotations

from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.policy import evaluate_threadwake_policy
from whooshd.runtime.threadwake.types import ThreadWakeMode, ThreadWakeRequestConfig


def _config(min_tokens: int = 1) -> ThreadWakeRequestConfig:
    return ThreadWakeRequestConfig(
        enabled=True,
        mode=ThreadWakeMode.OBSERVE,
        scope="thread",
        min_stable_prefix_tokens=min_tokens,
    )


def test_multimodal_stable_prefix_is_ineligible():
    graph = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {
                "role": "system",
                "content": [{"type": "image_url", "image_url": {"url": "file://x.png"}}],
            },
            {"role": "user", "content": "Latest"},
        ],
    )

    observation = evaluate_threadwake_policy(graph, _config())

    assert observation.eligible is False
    assert observation.reason == "stable_prefix_contains_multimodal"


def test_missing_model_is_ineligible():
    graph = compile_prompt_graph(
        model_id=None,
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Latest"},
        ],
    )

    observation = evaluate_threadwake_policy(graph, _config())

    assert observation.eligible is False
    assert observation.reason == "model_id_missing"


def test_missing_backend_is_ineligible():
    graph = compile_prompt_graph(
        model_id="m",
        backend=None,
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Latest"},
        ],
    )

    observation = evaluate_threadwake_policy(graph, _config())

    assert observation.eligible is False
    assert observation.reason == "backend_missing"


def test_min_token_threshold_is_enforced():
    graph = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "short"},
            {"role": "user", "content": "Latest"},
        ],
    )

    observation = evaluate_threadwake_policy(graph, _config(min_tokens=1024))

    assert observation.eligible is False
    assert observation.reason == "stable_prefix_below_min_tokens"


def test_disabled_mode_returns_enabled_false():
    observation = evaluate_threadwake_policy(
        None,
        ThreadWakeRequestConfig(enabled=False, mode=ThreadWakeMode.OFF, scope="thread"),
    )

    assert observation.enabled is False
    assert observation.eligible is False
    assert observation.reason == "threadwake_disabled"

from __future__ import annotations

from copy import deepcopy

from whooshd.contracts import ChatCompletionRequest
from whooshd.http_forwarding import build_forward_body
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


def _request(**overrides) -> ChatCompletionRequest:
    data = {
        "model": "stub-model",
        "messages": [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "Latest prompt"},
        ],
        "threadwake": {
            "enabled": True,
            "mode": "observe",
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        },
    }
    data.update(overrides)
    return ChatCompletionRequest.model_validate(data)


def test_observe_request_returns_eligible_observation():
    metrics = ThreadWakeMetrics()
    observation = ThreadWakeManager(metrics=metrics).observe_request(
        _request(),
        backend="stub",
    )

    assert observation.enabled is True
    assert observation.mode.value == "observe"
    assert observation.eligible is True
    assert observation.cache_hit is False
    assert observation.estimated_prefill_reuse_tokens == observation.stable_prefix_tokens
    assert metrics.snapshot()["threadwake_eligible_total"] == 1


def test_no_raw_prompt_text_appears_in_observation_output():
    secret = "DO_NOT_LEAK_THREADWAKE_PROMPT"
    req = _request(messages=[
        {"role": "system", "content": secret},
        {"role": "user", "content": "Latest prompt"},
    ])

    observation = ThreadWakeManager(metrics=ThreadWakeMetrics()).observe_request(
        req,
        backend="stub",
    )

    dumped = observation.model_dump_json()
    assert secret not in dumped
    assert "Latest prompt" not in dumped


def test_observe_mode_does_not_mutate_request_messages():
    req = _request()
    before = deepcopy(req.messages)

    ThreadWakeManager(metrics=ThreadWakeMetrics()).observe_request(req, backend="stub")

    assert req.messages == before


def test_disabled_request_returns_enabled_false():
    req = _request(threadwake={"enabled": False, "mode": "off"})

    observation = ThreadWakeManager(metrics=ThreadWakeMetrics()).observe_request(
        req,
        backend="stub",
    )

    assert observation.enabled is False
    assert observation.eligible is False


def test_request_mode_observe_enables_when_enabled_omitted():
    req = _request(threadwake={"mode": "observe", "min_stable_prefix_tokens": 1})

    observation = ThreadWakeManager(metrics=ThreadWakeMetrics()).observe_request(
        req,
        backend="stub",
    )

    assert observation.enabled is True
    assert observation.eligible is True


def test_threadwake_request_config_is_not_forwarded_upstream():
    req = _request()

    body = build_forward_body(req)

    assert "threadwake" not in body

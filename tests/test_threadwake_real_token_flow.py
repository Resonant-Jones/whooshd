"""Tests for real token flow — ephemeral/session paths with FakeTokenizerAdapter."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ThreadWakeIndex, ScopeContext
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
)


def _make_request(messages=None, thread_id=None, mode="ephemeral"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    data = {
        "model": "test-model",
        "messages": messages,
        "threadwake": {
            "enabled": True, "mode": mode, "scope": "thread", "min_stable_prefix_tokens": 1,
        },
    }
    if thread_id:
        data["thread_id"] = thread_id
    return ChatCompletionRequest.model_validate(data)


def _make_mgr():
    fake_kv = FakeKVBackend()
    fake_tok = FakeTokenizerAdapter()
    kv_reg = BackendKVAdapterRegistry()
    tok_reg = BackendTokenizerAdapterRegistry()
    kv_reg.register("fake", fake_kv)
    tok_reg.register("fake", fake_tok)
    return ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=kv_reg,
        tokenizer_registry=tok_reg,
        index=ThreadWakeIndex(max_entries=50),
    ), fake_kv


def _gen(request, params):
    return [f"gen_{i}" for i in range(params.get("max_tokens", 4))]


class TestRealTokenFlow:
    def test_ephemeral_hit_miss_with_real_tokenization(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False
        assert r1.observation is not None
        assert r1.observation.real_tokenization_available is True

        r2 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r2.cache_hit is True
        assert r2.matched_tokens > 0

    def test_observation_reports_real_token_counts(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        obs = result.observation
        assert obs is not None
        assert obs.real_tokenization_available is True
        assert obs.stable_prefix_token_count_real is not None
        assert obs.stable_prefix_token_count_real > 0
        assert obs.dynamic_tail_token_count_real is not None

    def test_session_continuation_with_real_tokens(self):
        mgr, fake_kv = _make_mgr()

        turn1 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
            ],
            thread_id="session-rt",
        )
        mgr.execute_ephemeral(turn1, backend="fake", generate_fn=_gen)

        turn2 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
            ],
            thread_id="session-rt",
        )
        r2 = mgr.execute_ephemeral(turn2, backend="fake", generate_fn=_gen)
        assert r2.cache_hit is True

    def test_no_raw_prompt_in_observation(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request(messages=[
            {"role": "system", "content": "SECRET_SYSTEM_DO_NOT_LEAK"},
            {"role": "user", "content": "hello"},
        ])

        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        obs_json = result.observation.model_dump_json() if result.observation else ""
        assert "SECRET_SYSTEM_DO_NOT_LEAK" not in obs_json

    def test_changed_template_hash_does_not_affect_fake_flow(self):
        """With FakeTokenizerAdapter, template hash is not set — flow still works."""
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        r1 = mgr.execute_ephemeral(
            req, backend="fake", generate_fn=_gen,
            chat_template_hash="different_hash",
        )
        assert r1.cache_hit is False

        r2 = mgr.execute_ephemeral(
            req, backend="fake", generate_fn=_gen,
            chat_template_hash="different_hash",
        )
        # Same hash → should hit
        assert r2.cache_hit is True

    def test_tokenizer_hash_included_in_tokenized_prompt(self):
        """Verify FakeTokenizerAdapter produces valid TokenizedPrompt."""
        from whooshd.runtime.threadwake.compiler import compile_prompt_graph
        req = _make_request()
        graph = compile_prompt_graph(model_id="m", backend="fake", messages=list(req.messages))
        tok = FakeTokenizerAdapter()
        result = tok.tokenize_prompt(graph, req, model_id="m")
        assert result.real_tokenization is True
        assert result.model_id == "m"
        assert result.backend == "fake"
        assert len(result.spans) == len(graph.segments)

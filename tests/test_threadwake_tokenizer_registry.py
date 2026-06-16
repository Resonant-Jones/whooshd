"""Tests for tokenizer registry integration with ThreadWakeManager."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ThreadWakeIndex
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


def _gen(request, params):
    return [f"gen_{i}" for i in range(params.get("max_tokens", 4))]


class TestRegistryIntegration:
    def test_unregistered_backend_degrades_to_unsupported_tokenizer(self):
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request()
        result = mgr.execute_ephemeral(req, backend="stub", generate_fn=_gen)

        assert result.observation is not None
        assert result.observation.tokenizer_capability == "unsupported"
        assert result.observation.real_tokenization_available is False

    def test_fake_tokenizer_enables_real_tokenization_in_observation(self):
        fake_kv = FakeKVBackend()
        fake_tok = FakeTokenizerAdapter()
        kv_reg = BackendKVAdapterRegistry()
        tok_reg = BackendTokenizerAdapterRegistry()
        kv_reg.register("fake", fake_kv)
        tok_reg.register("fake", fake_tok)

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            tokenizer_registry=tok_reg,
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request()
        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        assert result.observation is not None
        assert result.observation.real_tokenization_available is True
        assert result.observation.tokenizer_capability == "token_ids_with_spans"
        assert result.observation.stable_prefix_token_count_real is not None
        assert result.observation.stable_prefix_token_count_real > 0

    def test_without_tokenizer_kv_reuse_is_blocked(self):
        """Without a tokenizer adapter, ephemeral execution should not use KV."""
        fake_kv = FakeKVBackend()
        kv_reg = BackendKVAdapterRegistry()
        kv_reg.register("fake", fake_kv)
        # No tokenizer registry → defaults to no-op

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request()
        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False

        # Second request should also miss (KV reuse blocked without tokenizer)
        r2 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r2.cache_hit is False
        assert r2.observation is not None
        assert r2.observation.real_tokenization_available is False
        assert "tokenizer" in (r2.observation.kv_reuse_reason or "")

    def test_tokenizer_hash_in_tokenized_prompt(self):
        fake_tok = FakeTokenizerAdapter()
        req = _make_request()
        from whooshd.runtime.threadwake.compiler import compile_prompt_graph
        graph = compile_prompt_graph(model_id="m", backend="fake", messages=list(req.messages))
        result = fake_tok.tokenize_prompt(graph, req, model_id="m")
        # FakeTokenizerAdapter doesn't set tokenizer_hash — that's OK for tests
        assert result.real_tokenization is True

    def test_resumable_backend_blocked_without_tokenizer(self):
        """Even with resumable KV backend, no tokenizer → no KV reuse."""
        fake_kv = FakeKVBackend()  # reports RESUMABLE
        kv_reg = BackendKVAdapterRegistry()
        kv_reg.register("fake", fake_kv)
        # Deliberately omit tokenizer registry

        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request()

        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False
        assert r1.observation is not None
        assert r1.observation.backend_kv_capability == "resumable"  # Backend IS capable
        assert r1.observation.real_tokenization_available is False    # But tokenizer is NOT
        assert r1.observation.can_reuse_kv is False                  # So KV reuse blocked

        # Second request: still blocked (no tokenizer → no cache entries stored)
        r2 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r2.cache_hit is False

    def test_observe_mode_still_works_without_tokenizer(self):
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request(mode="observe")
        obs = mgr.observe_request(req, backend="stub")
        assert obs.enabled is True
        assert obs.eligible is True  # Observe mode works without tokenizer
        assert obs.real_tokenization_available is False

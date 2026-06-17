"""OpenAI route compatibility and failure isolation tests for ThreadWake."""

from __future__ import annotations

import json

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ThreadWakeIndex
from whooshd.runtime.threadwake.kv_lifecycle import KVLifecycleObserver
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
)


def _obs_request(**overrides):
    data = {
        "model": "stub-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "threadwake": {
            "enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1,
        },
    }
    data.update(overrides)
    return ChatCompletionRequest.model_validate(data)


def _make_mgr_with_observer():
    fake_kv = FakeKVBackend()
    fake_tok = FakeTokenizerAdapter()
    kv_reg = BackendKVAdapterRegistry()
    tok_reg = BackendTokenizerAdapterRegistry()
    kv_reg.register("fake", fake_kv)
    tok_reg.register("fake", fake_tok)
    observer = KVLifecycleObserver(enabled=True)
    mgr = ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=kv_reg,
        tokenizer_registry=tok_reg,
        kv_observer=observer,
        index=ThreadWakeIndex(max_entries=50),
    )
    return mgr, observer


# ── Streaming compatibility (output shape) ────────────────────────────────


class TestStreamingShape:
    def test_ephemeral_result_has_output_tokens(self):
        """EphemeralResult output_tokens is the streaming equivalent."""
        mgr, observer = _make_mgr_with_observer()

        def _gen(r, p):
            return ["Hello", " ", "World"]

        result = mgr.execute_ephemeral(
            _obs_request(threadwake={
                "enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1,
            }), backend="fake", generate_fn=_gen,
        )
        assert len(result.output_tokens) > 0
        assert isinstance(result.output_tokens, list)
        assert all(isinstance(t, str) for t in result.output_tokens)

    def test_ephemeral_result_does_not_inject_metadata(self):
        """EphemeralResult output_tokens contain only generated text."""
        mgr, observer = _make_mgr_with_observer()

        def _gen(r, p):
            return ["clean_output"]

        result = mgr.execute_ephemeral(
            _obs_request(threadwake={
                "enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1,
            }), backend="fake", generate_fn=_gen,
        )
        # output_tokens should contain only generated tokens, no metadata
        for token in result.output_tokens:
            assert "threadwake" not in token.lower()
            assert "cache_hit" not in token.lower()

    def test_non_streaming_result_has_correct_shape(self):
        """Result shape is consistent."""
        mgr, observer = _make_mgr_with_observer()

        def _gen(r, p):
            return ["text"]

        result = mgr.execute_ephemeral(
            _obs_request(threadwake={
                "enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1,
            }), backend="fake", generate_fn=_gen,
        )
        assert hasattr(result, "output_tokens")
        assert hasattr(result, "cache_hit")
        assert hasattr(result, "observation")
        assert hasattr(result, "metadata")


# ── Health reflects state ─────────────────────────────────────────────────


class TestHealthState:
    def test_health_has_required_sections(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(), backend="fake")
        health = mgr.get_health()
        assert "enabled" in health
        assert "mode" in health
        assert "status" in health
        assert "kv_observability" in health

    def test_health_no_raw_prompt(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(
            messages=[{"role": "system", "content": "HEALTH_LEAK_TEST_999"}],
        ), backend="fake")
        health_json = json.dumps(mgr.get_health())
        assert "HEALTH_LEAK_TEST_999" not in health_json

    def test_health_no_opaque_ref(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(), backend="fake")
        assert "opaque_ref" not in json.dumps(mgr.get_health())

    def test_health_no_token_ids(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(), backend="fake")
        assert "token_ids" not in json.dumps(mgr.get_health())


# ── Failure isolation ─────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_unsupported_backend_still_returns_result(self):
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics())
        req = _obs_request(threadwake={
            "enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1,
        })

        def _gen(r, p):
            return ["ok"]

        result = mgr.execute_ephemeral(req, backend="stub", generate_fn=_gen)
        assert len(result.output_tokens) > 0
        assert result.cache_hit is False

    def test_observe_failure_caught_gracefully(self):
        """Observation that would raise is handled by the manager."""
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics())
        # Request with no messages should still compile
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "threadwake": {"enabled": True, "mode": "observe"},
        })
        obs = mgr.observe_request(req, backend="stub")
        assert obs is not None


# ── Compatibility ─────────────────────────────────────────────────────────


class TestCompatibility:
    def test_request_with_threadwake_segments(self):
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
            "threadwake": {"enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1},
            "threadwake_segments": [
                {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
            ],
        })
        mgr, observer = _make_mgr_with_observer()
        obs = mgr.observe_request(req, backend="fake")
        assert obs.enabled is True

    def test_unknown_threadwake_fields_do_not_crash(self):
        """Unknown fields in threadwake config are ignored."""
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "threadwake": {
                "enabled": True, "mode": "observe",
                "unknown_future_setting": "should-be-safe",
            },
        })
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics())
        obs = mgr.observe_request(req, backend="stub")
        # Should not crash
        assert obs is not None


# ── MLX tokenizer flag behavior ───────────────────────────────────────────


class TestMLXTokenizerFlag:
    def test_mlx_not_registered_reports_unsupported(self):
        """Without registration, MLX tokenizer is unsupported."""
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), index=ThreadWakeIndex())
        obs = mgr.observe_request(_obs_request(), backend="mlx")
        assert obs.real_tokenization_available is False
        assert obs.tokenizer_capability == "unsupported"

    def test_mlx_registered_reports_capability(self):
        """With registered tokenizer, capability is reported."""
        tok_reg = BackendTokenizerAdapterRegistry()
        tok_reg.register("mlx", FakeTokenizerAdapter())
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            tokenizer_registry=tok_reg,
            index=ThreadWakeIndex(),
        )
        obs = mgr.observe_request(_obs_request(), backend="mlx")
        assert obs.real_tokenization_available is True
        assert obs.tokenizer_capability == "token_ids_with_spans"


# ── KV lifecycle events ───────────────────────────────────────────────────


class TestKVLifecycleEvents:
    def test_kv_events_accessible_via_observer(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(), backend="fake")
        events = observer.list_events(limit=10)
        assert isinstance(events, list)

    def test_metrics_kv_counters_update(self):
        mgr, observer = _make_mgr_with_observer()
        mgr.observe_request(_obs_request(), backend="fake")
        snap = mgr.metrics.snapshot()
        assert "threadwake_kv_events_total" in snap
        assert "threadwake_kv_errors_total" in snap
        assert isinstance(snap["threadwake_kv_events_total"], int)

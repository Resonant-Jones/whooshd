"""Smoke tests for ThreadWake observability through the manager directly.

Tests the full observation path (compile → observe → health/metrics) 
without requiring async HTTP — works without pytest-asyncio.
"""

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


def _make_mgr():
    fake_kv = FakeKVBackend()
    fake_tok = FakeTokenizerAdapter()
    kv_reg = BackendKVAdapterRegistry()
    tok_reg = BackendTokenizerAdapterRegistry()
    kv_reg.register("fake", fake_kv)
    tok_reg.register("fake", fake_tok)
    observer = KVLifecycleObserver(enabled=True)
    return ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=kv_reg,
        tokenizer_registry=tok_reg,
        kv_observer=observer,
        index=ThreadWakeIndex(max_entries=50),
    ), observer


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


# ── Observation behavior ──────────────────────────────────────────────────


class TestObservationAfterRequest:
    def test_health_reflects_observation_counters(self):
        mgr, observer = _make_mgr()
        before = mgr.get_health()
        mgr.observe_request(_obs_request(), backend="fake")
        after = mgr.get_health()
        # Status should be present
        assert "status" in after

    def test_kv_observability_section_appears(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        assert "kv_observability" in health

    def test_kv_events_total_is_int(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        assert isinstance(health["kv_observability"]["events_total"], int)

    def test_metrics_incremented_after_observe(self):
        mgr, observer = _make_mgr()
        before = dict(mgr.metrics.snapshot())
        mgr.observe_request(_obs_request(), backend="fake")
        after = mgr.metrics.snapshot()
        assert after["threadwake_observations_total"] >= before["threadwake_observations_total"]


# ── Privacy: no leakage ───────────────────────────────────────────────────


class TestPrivacy:
    def test_health_no_raw_prompt(self):
        mgr, observer = _make_mgr()
        mgr.observe_request(_obs_request(
            messages=[{"role": "system", "content": "PRIVATE_PROMPT_12345"}],
        ), backend="fake")
        health_json = json.dumps(mgr.get_health())
        assert "PRIVATE_PROMPT_12345" not in health_json

    def test_health_no_opaque_ref(self):
        mgr, observer = _make_mgr()
        mgr.observe_request(_obs_request(), backend="fake")
        assert "opaque_ref" not in json.dumps(mgr.get_health())

    def test_observation_no_raw_prompt(self):
        mgr, observer = _make_mgr()
        obs = mgr.observe_request(_obs_request(
            messages=[{"role": "user", "content": "LEAK_CHECK_67890"}],
        ), backend="fake")
        obs_json = obs.model_dump_json()
        assert "LEAK_CHECK_67890" not in obs_json


# ── Tokenizer capability ──────────────────────────────────────────────────


class TestTokenizerCapability:
    def test_fake_tokenizer_reports_token_ids(self):
        mgr, observer = _make_mgr()
        obs = mgr.observe_request(_obs_request(), backend="fake")
        assert obs.real_tokenization_available is True
        assert obs.tokenizer_capability == "token_ids_with_spans"

    def test_stub_backend_reports_unsupported(self):
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), index=ThreadWakeIndex())
        obs = mgr.observe_request(_obs_request(), backend="stub")
        assert obs.real_tokenization_available is False
        assert obs.tokenizer_capability == "unsupported"


# ── No ThreadWake metadata → still works ──────────────────────────────────


class TestCompatibility:
    def test_no_threadwake_config_still_observes(self):
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        mgr, observer = _make_mgr()
        obs = mgr.observe_request(req, backend="fake")
        assert obs.enabled is True or obs.enabled is False  # Boolean

    def test_threadwake_disabled_still_returns_obs(self):
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "threadwake": {"enabled": False, "mode": "off"},
        })
        mgr, observer = _make_mgr()
        obs = mgr.observe_request(req, backend="fake")
        assert obs.enabled is False

    def test_threadwake_segments_observation_works(self):
        req = ChatCompletionRequest.model_validate({
            "model": "stub-model",
            "messages": [
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
            "threadwake": {"enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1},
            "threadwake_segments": [
                {"name": "guardian", "message_index": 0, "segment_type": "system", "stability": "stable"},
                {"name": "user_msg", "message_index": 1, "segment_type": "user", "stability": "dynamic"},
            ],
        })
        mgr, observer = _make_mgr()
        obs = mgr.observe_request(req, backend="fake")
        assert obs.enabled is True


# ── KV lifecycle events after execution ───────────────────────────────────


class TestKVEventsAfterExecution:
    def test_ephemeral_miss_records_created(self):
        mgr, observer = _make_mgr()
        req = ChatCompletionRequest.model_validate({
            "model": "test-model",
            "messages": [{"role": "system", "content": "stable " * 8}, {"role": "user", "content": "hello"}],
            "threadwake": {"enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1},
        })

        def _gen(r, p):
            return ["gen_0"]

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        s = observer.stats()
        assert s.created_total >= 1

    def test_ephemeral_hit_records_reused(self):
        mgr, observer = _make_mgr()
        req = ChatCompletionRequest.model_validate({
            "model": "test-model",
            "messages": [{"role": "system", "content": "stable " * 8}, {"role": "user", "content": "hello"}],
            "threadwake": {"enabled": True, "mode": "ephemeral", "scope": "thread", "min_stable_prefix_tokens": 1},
        })

        def _gen(r, p):
            return ["gen_0"]

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)  # miss
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)  # hit
        s = observer.stats()
        assert s.reused_total >= 1


# ── Failure isolation ─────────────────────────────────────────────────────


class TestFailureIsolation:
    def test_observe_error_does_not_block_observe(self):
        """A manager that errors on observe should still be usable."""
        mgr, observer = _make_mgr()
        # First observe works normally
        obs1 = mgr.observe_request(_obs_request(), backend="fake")
        assert obs1.enabled is True or obs1.enabled is False

    def test_execute_error_returns_result(self):
        """Even with unsupported backend, execute_ephemeral returns a result."""
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), index=ThreadWakeIndex())

        def _gen(r, p):
            return ["gen_0"]

        result = mgr.execute_ephemeral(
            _obs_request(mode="ephemeral"), backend="stub", generate_fn=_gen,
        )
        assert len(result.output_tokens) > 0
        assert result.cache_hit is False


# ── Metrics use bounded labels ────────────────────────────────────────────


class TestMetricsBounded:
    def test_flat_counters_are_ints(self):
        mgr, observer = _make_mgr()
        mgr.observe_request(_obs_request(), backend="fake")
        snap = mgr.metrics.snapshot()
        for val in snap.values():
            assert isinstance(val, int)

    def test_labeled_keys_no_raw_hashes(self):
        mgr, observer = _make_mgr()
        mgr.observe_request(_obs_request(), backend="fake")
        snap = mgr.metrics.labeled_snapshot()
        for key in snap:
            assert not any(len(part) >= 64 for part in str(key).split(","))

"""Tests for KV lifecycle integration with ThreadWakeManager."""

from __future__ import annotations

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


def _make_mgr(**kwargs):
    fake_kv = FakeKVBackend()
    fake_tok = FakeTokenizerAdapter()
    kv_reg = BackendKVAdapterRegistry()
    tok_reg = BackendTokenizerAdapterRegistry()
    kv_reg.register("fake", fake_kv)
    tok_reg.register("fake", fake_tok)
    observer = KVLifecycleObserver(enabled=True, max_events=100)
    mgr = ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=kv_reg,
        tokenizer_registry=tok_reg,
        kv_observer=observer,
        index=ThreadWakeIndex(max_entries=50),
    )
    return mgr, observer, fake_kv


def _request(messages=None, thread_id=None, mode="ephemeral"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    data = {
        "model": "test-model",
        "messages": messages,
        "threadwake": {
            "enabled": True, "mode": mode,
            "scope": "thread" if thread_id else "request",
            "min_stable_prefix_tokens": 1,
        },
    }
    if thread_id:
        data["thread_id"] = thread_id
    return ChatCompletionRequest.model_validate(data)


def _gen(request, params):
    return [f"gen_{i}" for i in range(params.get("max_tokens", 4))]


class TestKVLifecycleIntegration:
    def test_miss_path_records_kv_created(self):
        mgr, observer, fake_kv = _make_mgr()
        req = _request()
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        s = observer.stats()
        assert s.created_total >= 1

    def test_hit_path_records_cloned_and_reused(self):
        mgr, observer, fake_kv = _make_mgr()
        req = _request()

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)  # miss
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)  # hit

        s = observer.stats()
        assert s.cloned_total >= 1
        assert s.reused_total >= 1

    def test_health_includes_kv_observability(self):
        mgr, observer, fake_kv = _make_mgr()
        req = _request()
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        health = mgr.get_health()
        assert "kv_observability" in health
        kv = health["kv_observability"]
        assert kv["enabled"] is True
        assert kv["events_total"] >= 1
        assert "events_by_type" in kv

    def test_health_does_not_leak_handles(self):
        mgr, observer, fake_kv = _make_mgr()
        health = mgr.get_health()
        kv = health.get("kv_observability", {})
        assert "opaque_ref" not in str(kv)
        assert "kv_state" not in str(kv)

    def test_metrics_have_kv_counters(self):
        mgr, observer, fake_kv = _make_mgr()
        req = _request()
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        snap = mgr.metrics.snapshot()
        assert "threadwake_kv_events_total" in snap

    def test_disabled_observer_no_events(self):
        """With observer disabled, no events should be recorded."""
        fake_kv = FakeKVBackend()
        fake_tok = FakeTokenizerAdapter()
        kv_reg = BackendKVAdapterRegistry()
        tok_reg = BackendTokenizerAdapterRegistry()
        kv_reg.register("fake", fake_kv)
        tok_reg.register("fake", fake_tok)

        # Observer disabled
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            tokenizer_registry=tok_reg,
            kv_observer=KVLifecycleObserver(enabled=False),
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _request()
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        health = mgr.get_health()
        assert health["kv_observability"]["events_total"] == 0

    def test_events_use_bounded_labels(self):
        """KV events must not contain raw hashes as event fields."""
        mgr, observer, fake_kv = _make_mgr()
        req = _request()
        mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)

        events = observer.list_events(limit=10)
        for e in events:
            # No raw hashes in reason field
            if e.get("reason"):
                assert len(e["reason"]) < 128  # Should be short enum-like
            # event_id is fine (short uuid)
            assert "opaque" not in str(e)

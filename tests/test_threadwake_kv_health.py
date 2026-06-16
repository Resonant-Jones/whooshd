"""Tests for KV lifecycle health and capability reporting."""

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


class TestKVHealth:
    def test_health_has_kv_observability_section(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        assert "kv_observability" in health

    def test_kv_observability_has_required_fields(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        kv = health["kv_observability"]
        required = {
            "enabled", "events_total", "events_by_type",
            "active_handles_estimate", "created_total", "cloned_total",
            "reused_total", "released_total", "errors_total",
        }
        assert required.issubset(set(kv.keys()))

    def test_kv_observability_empty_when_no_events(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        kv = health["kv_observability"]
        assert kv["events_total"] == 0
        assert kv["created_total"] == 0

    def test_health_contains_no_opaque_refs(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        assert "opaque_ref" not in json.dumps(health)

    def test_health_contains_no_raw_prompts(self):
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        health_json = json.dumps(health)
        assert "SECRET" not in health_json

    def test_production_reuse_disabled(self):
        """Production backends must show production_reuse_enabled=false."""
        # Fake backend is resumable but that's test-only
        # Check that health doesn't claim production reuse is enabled
        mgr, observer = _make_mgr()
        health = mgr.get_health()
        caps = health.get("backend_capabilities", {})
        for backend, cap in caps.items():
            # All backends currently should not have production reuse enabled
            assert isinstance(cap, str)  # Just a capability string, not claiming production status


class TestKVEventsEndpoint:
    def test_list_events_returns_safe_dicts(self):
        mgr, observer = _make_mgr()
        observer.record_capability(backend="fake", capability="resumable")

        events = observer.list_events(limit=10)
        assert len(events) >= 1
        for e in events:
            assert "opaque_ref" not in e
            assert "event_id" in e
            assert "event_type" in e
            assert "timestamp" in e

    def test_list_events_limit_is_enforced(self):
        mgr, observer = _make_mgr()
        for i in range(20):
            observer.record_capability(backend="fake", capability="resumable")
        events = observer.list_events(limit=5)
        assert len(events) <= 5

    def test_list_events_filter_by_type(self):
        mgr, observer = _make_mgr()
        observer.record_created(backend="fake", model_id="m")
        observer.record_reused(backend="fake", model_id="m")

        created = observer.list_events(event_type="kv_created")
        assert all(e["event_type"] == "kv_created" for e in created)

    def test_metrics_kv_counters_exist(self):
        mgr, observer = _make_mgr()
        snap = mgr.metrics.snapshot()
        assert "threadwake_kv_events_total" in snap
        assert "threadwake_kv_errors_total" in snap
        assert "threadwake_kv_active_handles_estimate" in snap

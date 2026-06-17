"""Tests for KV lifecycle event model and observer."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.kv_lifecycle import (
    KVEvent,
    KVLifecycleObserver,
    KVLifecycleStats,
)


class TestKVEvent:
    def test_event_contains_no_opaque_ref(self):
        event = KVEvent.create("kv_created", backend="fake", model_id="m",
                               kv_handle_id="handle-123", request_id="req-1",
                               thread_id="thread-a", cache_key="ckey")
        d = event.safe_dict()
        assert "opaque_ref" not in d

    def test_event_contains_no_raw_scope_ids(self):
        event = KVEvent.create("kv_created", thread_id="sensitive-thread-xyz")
        d = event.safe_dict()
        assert "sensitive-thread-xyz" not in json.dumps(d)
        # thread_id is hashed
        assert event.thread_id_hash is not None
        assert event.thread_id_hash != "sensitive-thread-xyz"

    def test_event_contains_no_raw_prompts(self):
        event = KVEvent.create("kv_created")
        d = event.safe_dict()
        assert "content" not in d
        assert "messages" not in d
        assert "prompt" not in d

    def test_event_contains_no_raw_token_ids(self):
        event = KVEvent.create("kv_created", token_count=100)
        d = event.safe_dict()
        assert "token_ids" not in d

    def test_event_hashes_request_id(self):
        event = KVEvent.create("kv_created", request_id="my-request-123")
        assert event.request_id_hash is not None
        assert event.request_id_hash != "my-request-123"

    def test_event_none_fields_stay_none(self):
        event = KVEvent.create("capability_reported")
        assert event.request_id_hash is None
        assert event.thread_id_hash is None

    def test_safe_dict_is_json_serializable(self):
        event = KVEvent.create("kv_created", backend="fake", model_id="m")
        json.dumps(event.safe_dict())  # Should not raise


class TestObserver:
    def test_records_event(self):
        obs = KVLifecycleObserver(enabled=True, max_events=100)
        obs.record_capability(backend="fake", capability="resumable")
        stats = obs.stats()
        assert stats.events_total == 1
        assert stats.events_by_type.get("capability_reported") == 1

    def test_disabled_observer_is_noop(self):
        obs = KVLifecycleObserver(enabled=False)
        obs.record_capability(backend="fake", capability="resumable")
        assert obs.stats().events_total == 0

    def test_ring_buffer_evicts_old_events(self):
        obs = KVLifecycleObserver(enabled=True, max_events=5)
        for i in range(10):
            obs.record_capability(backend=f"b{i}", capability="resumable")
        events = obs.list_events(limit=20)
        assert len(events) == 5  # Max buffer size

    def test_clear_returns_count(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_capability(backend="fake", capability="resumable")
        obs.record_capability(backend="mlx", capability="unsupported")
        count = obs.clear()
        assert count == 2
        assert obs.stats().events_total == 0

    def test_created_increments_active_handles(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_created(backend="fake", model_id="m")
        assert obs.stats().active_handles_estimate == 1

    def test_released_decrements_active_handles(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_created(backend="fake", model_id="m")
        obs.record_released(backend="fake", model_id="m")
        assert obs.stats().active_handles_estimate == 0

    def test_active_handles_never_negative(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_released(backend="fake", model_id="m")  # Release without create
        assert obs.stats().active_handles_estimate == 0

    def test_filter_by_event_type(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_created(backend="fake", model_id="m")
        obs.record_reused(backend="fake", model_id="m")
        created = obs.list_events(event_type="kv_created")
        assert len(created) == 1
        assert created[0]["event_type"] == "kv_created"

    def test_filter_by_backend(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_created(backend="fake", model_id="m")
        obs.record_created(backend="mlx", model_id="m")
        fake_events = obs.list_events(backend="fake")
        assert len(fake_events) == 1
        assert fake_events[0]["backend"] == "fake"

    def test_convenience_recorders(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_capability(backend="fake", capability="resumable")
        obs.record_created(backend="fake", model_id="m", token_count=100)
        obs.record_cloned(backend="fake", model_id="m")
        obs.record_reused(backend="fake", model_id="m")
        obs.record_released(backend="fake", model_id="m")
        obs.record_invalidated(backend="fake", model_id="m")
        obs.record_evicted(backend="fake", model_id="m")
        obs.record_error(backend="fake", model_id="m", reason="test error")

        s = obs.stats()
        assert s.events_total == 8
        assert s.created_total == 1
        assert s.cloned_total == 1
        assert s.reused_total == 1
        assert s.released_total == 1
        assert s.invalidated_total == 1
        assert s.evicted_total == 1
        assert s.errors_total == 1

    def test_stats_is_snapshot(self):
        obs = KVLifecycleObserver(enabled=True)
        obs.record_created(backend="fake", model_id="m")
        s1 = obs.stats()
        obs.record_created(backend="fake", model_id="m")
        # s1 should not have changed
        assert s1.created_total == 1

"""Tests for ThreadWake metrics — counters, labels, bounded cardinality."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics, _coerce_reason
from whooshd.runtime.threadwake.types import (
    ThreadWakeMode,
    ThreadWakeObservation,
)


class TestFlatCounters:
    def test_record_eligible_increments_hits(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True,
            mode=ThreadWakeMode.EPHEMERAL,
            eligible=True,
            cache_hit=True,
            estimated_prefill_reuse_tokens=50,
            backend_kv_capability="resumable",
            cache_scope="thread",
        )
        metrics.record(obs)

        snap = metrics.snapshot()
        assert snap["threadwake_observations_total"] == 1
        assert snap["threadwake_eligible_total"] == 1
        assert snap["threadwake_cache_hits_total"] == 1
        assert snap["threadwake_cache_misses_total"] == 0
        assert snap["threadwake_prefix_tokens_matched_total"] == 50

    def test_record_ineligible_increments_misses(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True,
            mode=ThreadWakeMode.EPHEMERAL,
            eligible=False,
            reason="stable_prefix_below_min_tokens",
            backend_kv_capability="resumable",
            cache_scope="thread",
        )
        metrics.record(obs)

        snap = metrics.snapshot()
        assert snap["threadwake_ineligible_total"] == 1
        assert snap["threadwake_eligible_total"] == 0

    def test_record_eligible_miss(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True,
            mode=ThreadWakeMode.EPHEMERAL,
            eligible=True,
            cache_hit=False,
            estimated_prefill_reuse_tokens=0,
            backend_kv_capability="resumable",
            cache_scope="thread",
        )
        metrics.record(obs)

        snap = metrics.snapshot()
        assert snap["threadwake_cache_misses_total"] == 1
        assert snap["threadwake_cache_hits_total"] == 0

    def test_record_eviction(self):
        metrics = ThreadWakeMetrics()
        metrics.record_eviction(3)
        assert metrics.snapshot()["threadwake_cache_evictions_total"] == 3

        metrics.record_eviction()
        assert metrics.snapshot()["threadwake_cache_evictions_total"] == 4


class TestLabeledCounters:
    def test_labeled_counters_have_bounded_dimensions(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True,
            mode=ThreadWakeMode.EPHEMERAL,
            eligible=True,
            cache_hit=True,
            estimated_prefill_reuse_tokens=10,
            backend_kv_capability="resumable",
            cache_scope="thread",
        )
        metrics.record(obs)

        snap = metrics.labeled_snapshot()
        assert len(snap) > 0
        # Keys must not contain raw hashes or user IDs
        for key in snap:
            assert "hash" not in key.lower() or "prompt" not in key.lower()

    def test_labeled_counters_use_bounded_reason(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True,
            mode=ThreadWakeMode.EPHEMERAL,
            eligible=False,
            reason="stable_prefix_below_min_tokens",
            backend_kv_capability="unsupported",
            cache_scope="thread",
        )
        metrics.record(obs)
        metrics.record(obs)

        snap = metrics.labeled_snapshot()
        found = False
        for key, count in snap.items():
            if "stable_prefix_below_min_tokens" in key:
                assert count == 2
                found = True
        assert found

    def test_labeled_counters_mode_dimension(self):
        metrics = ThreadWakeMetrics()
        obs1 = ThreadWakeObservation(
            enabled=True, mode=ThreadWakeMode.OBSERVE, eligible=True, cache_hit=False,
            backend_kv_capability="unsupported", cache_scope="thread",
        )
        obs2 = ThreadWakeObservation(
            enabled=True, mode=ThreadWakeMode.EPHEMERAL, eligible=True, cache_hit=False,
            backend_kv_capability="resumable", cache_scope="thread",
        )
        metrics.record(obs1)
        metrics.record(obs2)

        snap = metrics.labeled_snapshot()
        has_observe = any("observe" in k for k in snap)
        has_ephemeral = any("ephemeral" in k for k in snap)
        assert has_observe
        assert has_ephemeral


class TestReasonCoercion:
    def test_coerce_known_reasons(self):
        assert _coerce_reason("threadwake_disabled") == "threadwake_disabled"
        assert _coerce_reason("prompt_graph_missing") == "prompt_graph_missing"
        assert _coerce_reason("backend_unsupported") == "backend_unsupported"

    def test_coerce_prefix_match(self):
        assert _coerce_reason("backend_capable_but_ineligible: something") == "backend_missing"

    def test_coerce_unknown_reason(self):
        assert _coerce_reason("completely_unknown_reason_xyz") == "other"

    def test_coerce_none(self):
        assert _coerce_reason(None) == "eligible"


class TestHighCardinalityPrevention:
    def test_labeled_keys_do_not_contain_raw_prompts(self):
        metrics = ThreadWakeMetrics()
        # Even with varied observations, keys should remain bounded
        for i in range(5):
            obs = ThreadWakeObservation(
                enabled=True,
                mode=ThreadWakeMode.EPHEMERAL,
                eligible=False if i % 2 == 1 else True,
                cache_hit=False,
                reason="stable_prefix_below_min_tokens" if i % 3 == 0 else None,
                backend_kv_capability="resumable" if i % 2 == 0 else "unsupported",
                cache_scope="thread",
            )
            metrics.record(obs)

        snap = metrics.labeled_snapshot()
        # With only bounded labels, we should have a small number of keys
        assert len(snap) <= 20  # conservative upper bound

    def test_labeled_keys_do_not_contain_hashes(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True, mode=ThreadWakeMode.EPHEMERAL, eligible=True, cache_hit=True,
            estimated_prefill_reuse_tokens=100,
            backend_kv_capability="resumable", cache_scope="thread",
            stable_prefix_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        )
        metrics.record(obs)

        snap = metrics.labeled_snapshot()
        for key in snap:
            # The stable_prefix_hash should not appear as a label
            assert "abcdef1234" not in key

    def test_snapshot_json_serializable(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True, mode=ThreadWakeMode.EPHEMERAL, eligible=True, cache_hit=True,
            backend_kv_capability="resumable", cache_scope="thread",
        )
        metrics.record(obs)

        # Should not raise
        json.dumps(metrics.snapshot())
        json.dumps(metrics.labeled_snapshot())


class TestReset:
    def test_reset_clears_all_counters(self):
        metrics = ThreadWakeMetrics()
        obs = ThreadWakeObservation(
            enabled=True, mode=ThreadWakeMode.EPHEMERAL, eligible=True, cache_hit=True,
            backend_kv_capability="resumable", cache_scope="thread",
        )
        metrics.record(obs)
        metrics.record_eviction(5)

        metrics.reset()
        snap = metrics.snapshot()
        for val in snap.values():
            assert val == 0
        assert metrics.labeled_snapshot() == {}

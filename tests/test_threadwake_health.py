"""Tests for ThreadWake health endpoint and manager health/flush methods."""

from __future__ import annotations

import json

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


# ── helpers ────────────────────────────────────────────────────────────────


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


def _make_mgr(**index_kwargs) -> ThreadWakeManager:
    index = ThreadWakeIndex(**index_kwargs)
    return ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        index=index,
    )


# ── Health ─────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_expected_fields(self):
        mgr = _make_mgr()
        health = mgr.get_health()

        required = {
            "enabled", "mode", "status",
            "entry_count", "ready_entries", "stale_entries",
            "max_entries",
            "estimated_memory_bytes", "max_memory_bytes",
            "total_hits", "total_misses", "total_evictions",
            "global_allowed",
            "backend_capabilities",
            "entries_by_status", "entries_by_scope",
        }
        assert required.issubset(set(health.keys()))

    def test_health_reflects_index_state(self):
        mgr = _make_mgr()
        # Two eligible observations
        mgr.observe_request(
            _request(messages=[
                {"role": "system", "content": "Stable " * 8},
                {"role": "user", "content": "hello"},
            ]),
            backend="stub",
        )
        mgr.observe_request(
            _request(messages=[
                {"role": "system", "content": "Stable " * 8},
                {"role": "user", "content": "hello again"},
            ]),
            backend="stub",
        )

        health = mgr.get_health()
        assert health["entry_count"] >= 1  # Same prompt prefix → same key = 1 entry
        assert health["mode"] in ("observe", "off")  # depends on env config
        # enabled depends on env — must be present
        assert "enabled" in health

    def test_health_does_not_include_raw_prompt_content(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(messages=[
                {"role": "system", "content": "SECRET_DO_NOT_LEAK"},
                {"role": "user", "content": "hello"},
            ]),
            backend="stub",
        )

        health = mgr.get_health()
        health_json = json.dumps(health)
        assert "SECRET_DO_NOT_LEAK" not in health_json

    def test_health_does_not_include_opaque_refs(self):
        mgr = _make_mgr()
        health = mgr.get_health()
        health_json = json.dumps(health)
        assert "opaque_ref" not in health_json

    def test_global_allowed_reflects_config(self):
        mgr_default = _make_mgr()
        assert mgr_default.get_health()["global_allowed"] is False

        mgr_global = _make_mgr(allow_global=True)
        assert mgr_global.get_health()["global_allowed"] is True

    def test_health_empty_index_shows_zeroes(self):
        mgr = _make_mgr()
        health = mgr.get_health()
        assert health["entry_count"] == 0
        assert health["total_hits"] == 0
        assert health["total_misses"] == 0
        assert health["total_evictions"] == 0


# ── Flush ──────────────────────────────────────────────────────────────────


class TestFlush:
    def test_flush_removes_entries_and_reports_count(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(messages=[
                {"role": "system", "content": "System prompt " * 8},
                {"role": "user", "content": "query"},
            ]),
            backend="stub",
        )

        assert mgr.get_health()["entry_count"] >= 1
        result = mgr.flush_cache()
        assert result["flushed"] >= 1
        assert mgr.get_health()["entry_count"] == 0

    def test_flush_by_scope_only_removes_matching(self):
        mgr = _make_mgr(max_entries=50)
        # Thread-scoped observation
        mgr.observe_request(
            _request(
                messages=[
                    {"role": "system", "content": "System prompt A " * 8},
                    {"role": "user", "content": "query a"},
                ],
                threadwake={"enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1},
            ),
            backend="stub",
        )
        # Request-scoped observation
        mgr.observe_request(
            _request(
                messages=[
                    {"role": "system", "content": "System prompt B " * 8},
                    {"role": "user", "content": "query b"},
                ],
                threadwake={"enabled": True, "mode": "observe", "scope": "request", "min_stable_prefix_tokens": 1},
            ),
            backend="stub",
        )

        # Verify both scopes exist
        health_before = mgr.get_health()
        assert health_before["entry_count"] >= 2

        # Flush only thread scope
        result = mgr.flush_cache(scope="thread")
        assert result["flushed"] >= 1

        health_after = mgr.get_health()
        assert health_after["entry_count"] >= 1  # request-scoped remains

    def test_flush_empty_index_returns_zero(self):
        mgr = _make_mgr()
        result = mgr.flush_cache()
        assert result["flushed"] == 0

    def test_flush_unknown_scope_removes_zero(self):
        mgr = _make_mgr()
        mgr.observe_request(_request(), backend="stub")
        result = mgr.flush_cache(scope="nonexistent_scope")
        assert result["flushed"] == 0
        assert mgr.get_health()["entry_count"] >= 1


# ── Manager integration ────────────────────────────────────────────────────


class TestManagerIndexIntegration:
    def test_eligible_observation_adds_to_index(self):
        mgr = _make_mgr()
        # Before: empty
        assert mgr.get_health()["entry_count"] == 0

        mgr.observe_request(
            _request(messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "query"},
            ]),
            backend="stub",
        )

        # After: one entry
        assert mgr.get_health()["entry_count"] == 1

    def test_ineligible_observation_does_not_add_to_index(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(
                messages=[{"role": "user", "content": "short"}],
                threadwake={
                    "enabled": True,
                    "mode": "observe",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 999999,  # impossibly high
                },
            ),
            backend="stub",
        )

        assert mgr.get_health()["entry_count"] == 0

    def test_disabled_request_does_not_add_to_index(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(threadwake={"enabled": False, "mode": "off"}),
            backend="stub",
        )

        assert mgr.get_health()["entry_count"] == 0

    def test_repeated_same_prompt_updates_existing_entry(self):
        mgr = _make_mgr()
        req = _request(messages=[
            {"role": "system", "content": "Stable " * 8},
            {"role": "user", "content": "hello"},
        ])

        mgr.observe_request(req, backend="stub")
        mgr.observe_request(req, backend="stub")

        # Same cache key → 1 entry (updated, not duplicated)
        assert mgr.get_health()["entry_count"] == 1

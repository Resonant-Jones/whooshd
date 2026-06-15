"""Tests for ThreadWake admin flush — scope/model_id/scope_id filtering."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


def _request(messages=None, threadwake_config=None, thread_id=None, model="stub-model"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    if threadwake_config is None:
        threadwake_config = {
            "enabled": True,
            "mode": "observe",
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        }
    data = {
        "model": model,
        "messages": messages,
        "threadwake": threadwake_config,
    }
    if thread_id:
        data["thread_id"] = thread_id
    return ChatCompletionRequest.model_validate(data)


def _make_mgr(**index_kwargs) -> ThreadWakeManager:
    index = ThreadWakeIndex(max_entries=50, **index_kwargs)
    return ThreadWakeManager(metrics=ThreadWakeMetrics(), index=index)


class TestFlushAll:
    def test_flush_all_clears_all_entries(self):
        mgr = _make_mgr()
        mgr.observe_request(_request(messages=[
            {"role": "system", "content": "System A " * 8},
            {"role": "user", "content": "hello a"},
        ]), backend="stub")
        mgr.observe_request(_request(messages=[
            {"role": "system", "content": "System B " * 8},
            {"role": "user", "content": "hello b"},
        ]), backend="stub")

        assert mgr.get_health()["entry_count"] == 2

        result = mgr.flush_cache()
        assert result["flushed"] == 2
        assert result["remaining"] == 0
        assert mgr.get_health()["entry_count"] == 0

    def test_flush_all_reports_remaining_zero(self):
        mgr = _make_mgr()
        mgr.observe_request(_request(), backend="stub")
        result = mgr.flush_cache()
        assert result["remaining"] == 0


class TestFlushByScope:
    def test_flush_thread_clears_thread_entries(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(
                messages=[
                    {"role": "system", "content": "System A " * 8},
                    {"role": "user", "content": "hello a"},
                ],
                threadwake_config={"enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1},
            ),
            backend="stub",
        )
        mgr.observe_request(
            _request(
                messages=[
                    {"role": "system", "content": "System B " * 8},
                    {"role": "user", "content": "hello b"},
                ],
                threadwake_config={"enabled": True, "mode": "observe", "scope": "request", "min_stable_prefix_tokens": 1},
            ),
            backend="stub",
        )

        result = mgr.flush_cache(scope="thread")
        assert result["flushed"] == 1
        assert result["remaining"] == 1

        # Request-scoped entry should remain
        health = mgr.get_health()
        assert health["entry_count"] == 1

    def test_flush_project_clears_project_entries(self):
        mgr = _make_mgr()
        # Thread entry
        mgr.observe_request(
            _request(
                messages=[
                    {"role": "system", "content": "System A " * 8},
                    {"role": "user", "content": "hello a"},
                ],
                threadwake_config={"enabled": True, "mode": "observe", "scope": "thread", "min_stable_prefix_tokens": 1},
            ),
            backend="stub",
        )
        # Project entry — not easily created via observe_request (only works with scope="thread" or "request")
        # Use index directly
        from whooshd.runtime.threadwake.keys import sha256_hex
        mgr._index.put_observation(
            cache_key="project-key",
            model_id="test-model",
            backend="stub",
            prompt_prefix_hash="abc",
            token_count=100,
            scope="project",
            scope_context=ScopeContext(project_id="p1"),
        )

        result = mgr.flush_cache(scope="project")
        assert result["flushed"] == 1
        assert result["remaining"] == 1  # Thread entry remains


class TestFlushByModelId:
    def test_flush_by_model_id_only_removes_matching(self):
        mgr = _make_mgr()
        mgr.observe_request(
            _request(model="model-a", messages=[
                {"role": "system", "content": "System A " * 8},
                {"role": "user", "content": "hello a"},
            ]),
            backend="stub",
        )
        mgr.observe_request(
            _request(model="model-b", messages=[
                {"role": "system", "content": "System B " * 8},
                {"role": "user", "content": "hello b"},
            ]),
            backend="stub",
        )

        result = mgr.flush_cache(model_id="model-a")
        assert result["flushed"] == 1
        assert result["remaining"] == 1

    def test_flush_by_unknown_model_id_removes_none(self):
        mgr = _make_mgr()
        mgr.observe_request(_request(model="model-a"), backend="stub")

        result = mgr.flush_cache(model_id="nonexistent")
        assert result["flushed"] == 0
        assert result["remaining"] == 1


class TestFlushByScopeId:
    def test_flush_by_scope_id_removes_matching(self):
        index = ThreadWakeIndex(max_entries=50)
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), index=index)

        from whooshd.runtime.threadwake.keys import sha256_hex

        # Create an entry for a specific thread
        thread_scope_id = sha256_hex("thread-t1")
        index.put_observation(
            cache_key="key-1", model_id="m", backend="stub",
            prompt_prefix_hash="abc", token_count=100, scope="thread",
            scope_context=ScopeContext(thread_id="thread-t1"),
        )
        index.put_observation(
            cache_key="key-2", model_id="m", backend="stub",
            prompt_prefix_hash="def", token_count=100, scope="thread",
            scope_context=ScopeContext(thread_id="thread-t2"),
        )

        # Flush by scope_id (provides the raw thread_id, which gets hashed internally)
        result = mgr.flush_cache(scope="thread", scope_id="thread-t1")
        assert result["flushed"] == 1
        assert result["remaining"] == 1


class TestFlushDoesNotCrashActive:
    def test_flush_on_empty_index_returns_zeroes(self):
        mgr = _make_mgr()
        result = mgr.flush_cache()
        assert result["flushed"] == 0
        assert result["remaining"] == 0

    def test_flush_multiple_times_is_idempotent(self):
        mgr = _make_mgr()
        mgr.observe_request(_request(), backend="stub")

        r1 = mgr.flush_cache()
        assert r1["flushed"] == 1

        r2 = mgr.flush_cache()
        assert r2["flushed"] == 0  # Nothing left to flush

    def test_flush_combined_filters(self):
        index = ThreadWakeIndex(max_entries=50)
        mgr = ThreadWakeManager(metrics=ThreadWakeMetrics(), index=index)

        index.put_observation(
            cache_key="k1", model_id="model-a", backend="stub",
            prompt_prefix_hash="abc", token_count=100, scope="thread",
            scope_context=ScopeContext(thread_id="t1"),
        )
        index.put_observation(
            cache_key="k2", model_id="model-a", backend="stub",
            prompt_prefix_hash="def", token_count=100, scope="request",
            scope_context=ScopeContext(),
        )
        index.put_observation(
            cache_key="k3", model_id="model-b", backend="stub",
            prompt_prefix_hash="ghi", token_count=100, scope="thread",
            scope_context=ScopeContext(thread_id="t1"),
        )

        # Flush thread scope of model-a only
        result = mgr.flush_cache(scope="thread", model_id="model-a")
        assert result["flushed"] == 1
        assert result["remaining"] == 2

"""Tests for ThreadWake scope enforcement rules."""

from __future__ import annotations

import pytest

from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex


# ── helpers ────────────────────────────────────────────────────────────────


def _put(index: ThreadWakeIndex, cache_key: str = "key-1", **kwargs) -> None:
    defaults = {
        "cache_key": cache_key,
        "model_id": "test-model",
        "backend": "stub",
        "prompt_prefix_hash": "abc",
        "token_count": 100,
        "scope": "thread",
        "scope_context": ScopeContext(thread_id="thread-a"),
    }
    defaults.update(kwargs)
    index.put_observation(**defaults)


# ── thread scope ───────────────────────────────────────────────────────────


class TestThreadScope:
    def test_thread_scope_same_thread_hits(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext(thread_id="t1"))
        result = index.get("key-1", ScopeContext(thread_id="t1"))
        assert result is not None

    def test_thread_scope_different_thread_misses(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext(thread_id="t1"))
        result = index.get("key-1", ScopeContext(thread_id="t2"))
        assert result is None

    def test_thread_scope_no_thread_id_misses(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext(thread_id="t1"))
        result = index.get("key-1", ScopeContext())
        assert result is None

    def test_unscoped_thread_entry_never_matches_unscoped_lookup(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext())
        assert index.get("key-1", ScopeContext()) is None


# ── user scope ─────────────────────────────────────────────────────────────


class TestUserScope:
    def test_user_scope_same_user_hits(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="user", scope_context=ScopeContext(user_id="u1"))
        result = index.get("key-1", ScopeContext(user_id="u1"))
        assert result is not None

    def test_user_scope_different_user_misses(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="user", scope_context=ScopeContext(user_id="u1"))
        result = index.get("key-1", ScopeContext(user_id="u2"))
        assert result is None

    def test_user_scope_no_user_id_misses(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="user", scope_context=ScopeContext(user_id="u1"))
        result = index.get("key-1", ScopeContext())
        assert result is None


# ── project scope ──────────────────────────────────────────────────────────


class TestProjectScope:
    def test_project_scope_same_project_hits(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="project", scope_context=ScopeContext(project_id="p1"))
        result = index.get("key-1", ScopeContext(project_id="p1"))
        assert result is not None

    def test_project_scope_different_project_misses(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="project", scope_context=ScopeContext(project_id="p1"))
        result = index.get("key-1", ScopeContext(project_id="p2"))
        assert result is None


# ── request scope ──────────────────────────────────────────────────────────


class TestRequestScope:
    def test_request_scope_always_hits(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="request", scope_context=ScopeContext())
        result = index.get("key-1", ScopeContext(thread_id="t1"))
        assert result is not None

    def test_request_scope_different_context_still_hits(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="request", scope_context=ScopeContext())
        result = index.get("key-1", ScopeContext(thread_id="t2", user_id="u2"))
        assert result is not None


# ── global scope ───────────────────────────────────────────────────────────


class TestGlobalScope:
    def test_global_scope_disabled_by_default(self):
        index = ThreadWakeIndex(allow_global=False)
        with pytest.raises(ValueError, match="Global scope is not allowed"):
            _put(index, "key-1", scope="global", scope_context=ScopeContext())

    def test_global_scope_allowed_when_enabled(self):
        index = ThreadWakeIndex(allow_global=True)
        _put(index, "key-1", scope="global", scope_context=ScopeContext())
        result = index.get("key-1", ScopeContext(thread_id="t1"))
        assert result is not None

    def test_global_scope_misses_when_disabled(self):
        index = ThreadWakeIndex(allow_global=False)
        # Cannot even insert
        with pytest.raises(ValueError):
            _put(index, "key-1", scope="global", scope_context=ScopeContext())


# ── scope cross-contamination ──────────────────────────────────────────────


class TestScopeIsolation:
    def test_thread_entry_not_accessible_via_user_scope(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext(thread_id="t1"))

        # Lookup with user scope context should miss (scope mismatch)
        result = index.get("key-1", ScopeContext(user_id="u1"))
        assert result is None

    def test_user_entry_not_accessible_via_project_scope(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="user", scope_context=ScopeContext(user_id="u1"))

        result = index.get("key-1", ScopeContext(project_id="p1"))
        assert result is None

    def test_same_scope_different_key_does_not_match(self):
        index = ThreadWakeIndex()
        _put(index, "key-1", scope="thread", scope_context=ScopeContext(thread_id="t1"))

        # Different key, same scope — should miss
        result = index.get("key-2", ScopeContext(thread_id="t1"))
        assert result is None


# ── ScopeContext ───────────────────────────────────────────────────────────


class TestScopeContext:
    def test_fingerprint_is_deterministic(self):
        ctx1 = ScopeContext(thread_id="t1", user_id="u1")
        ctx2 = ScopeContext(thread_id="t1", user_id="u1")
        assert ctx1.fingerprint() == ctx2.fingerprint()

    def test_fingerprint_differs_for_different_ids(self):
        ctx1 = ScopeContext(thread_id="t1")
        ctx2 = ScopeContext(thread_id="t2")
        assert ctx1.fingerprint() != ctx2.fingerprint()

    def test_fingerprint_differs_for_different_user_ids(self):
        ctx1 = ScopeContext(user_id="u1")
        ctx2 = ScopeContext(user_id="u2")
        assert ctx1.fingerprint() != ctx2.fingerprint()

    def test_default_scope_context_has_none_fields(self):
        ctx = ScopeContext()
        assert ctx.thread_id is None
        assert ctx.user_id is None
        assert ctx.project_id is None

    def test_to_dict_includes_all_fields(self):
        ctx = ScopeContext(thread_id="t1", user_id="u1", project_id="p1")
        d = ctx.to_dict()
        assert d["thread_id"] == "t1"
        assert d["user_id"] == "u1"
        assert d["project_id"] == "p1"

"""Tests for ThreadWake ephemeral KV reuse — hit/miss flows with FakeKVBackend."""

from __future__ import annotations

import pytest

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend, NoOpKVBackendAdapter
from whooshd.runtime.threadwake.handles import KVCapability
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex, EntryStatus
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
from whooshd.runtime.threadwake.types import ThreadWakeMode, ThreadWakeRequestConfig


# ── helpers ────────────────────────────────────────────────────────────────


def _make_request(
    messages=None,
    model="test-model",
    threadwake_config=None,
    thread_id=None,
    user_id=None,
):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    if threadwake_config is None:
        threadwake_config = {
            "enabled": True,
            "mode": "ephemeral",
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
    if user_id:
        data["user_id"] = user_id
    return ChatCompletionRequest.model_validate(data)


def _make_mgr(**kwargs):
    fake_kv = FakeKVBackend()
    registry = BackendKVAdapterRegistry()
    registry.register("fake", fake_kv)
    index = ThreadWakeIndex(max_entries=50)
    mgr = ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=registry,
        index=index,
    )
    return mgr, fake_kv


def _generate_fn(request, params):
    """Simple generate_fn that returns deterministic output."""
    max_tokens = params.get("max_tokens", 4)
    return [f"gen_{i}" for i in range(max_tokens)]


# ── Basic hit/miss flow ────────────────────────────────────────────────────


class TestEphemeralHitMiss:
    def test_first_request_misses_and_stores_kv(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        result = mgr.execute_ephemeral(
            req, backend="fake", generate_fn=_generate_fn,
        )

        assert result.cache_hit is False
        assert result.matched_tokens == 0
        assert len(result.output_tokens) > 0
        # Prefill should have been called
        assert len(fake_kv.prefill_calls) >= 1

    def test_second_identical_request_hits(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        # First request: miss
        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        # Second request with identical messages: should hit
        r2 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        assert r2.cache_hit is True
        assert r2.matched_tokens > 0

    def test_hit_uses_generate_from_kv(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)  # miss
        fake_kv.generate_from_kv_calls.clear()
        fake_kv.prefill_calls.clear()

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)  # hit

        assert len(fake_kv.generate_from_kv_calls) == 1
        assert len(fake_kv.prefill_calls) == 0  # no new prefill on hit


# ── Different prefixes miss ────────────────────────────────────────────────


class TestPrefixChanges:
    def test_changed_system_prompt_misses(self):
        mgr, fake_kv = _make_mgr()

        req1 = _make_request(messages=[
            {"role": "system", "content": "System prompt A " * 8},
            {"role": "user", "content": "hello"},
        ])
        req2 = _make_request(messages=[
            {"role": "system", "content": "System prompt B " * 8},
            {"role": "user", "content": "hello"},
        ])

        r1 = mgr.execute_ephemeral(req1, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        r2 = mgr.execute_ephemeral(req2, backend="fake", generate_fn=_generate_fn)
        assert r2.cache_hit is False  # Different system prompt → miss

    def test_changed_latest_user_message_can_hit_stable_prefix(self):
        mgr, fake_kv = _make_mgr()

        req1 = _make_request(messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "first query"},
        ])
        req2 = _make_request(messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "second query"},
        ])

        r1 = mgr.execute_ephemeral(req1, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False  # miss — first time

        # Second request has same stable prefix but different latest user message
        # The stable prefix should still match (system prompt unchanged)
        r2 = mgr.execute_ephemeral(req2, backend="fake", generate_fn=_generate_fn)
        # May be hit or miss depending on whether the compiler considers
        # the latest user message as dynamic (not in stable prefix).
        # With only system+user, system is the stable prefix and user is dynamic.
        # The cache key is built from the stable prefix, so it should HIT.
        assert r2.cache_hit is True


# ── Different model misses ─────────────────────────────────────────────────


class TestModelChanges:
    def test_different_model_misses(self):
        mgr, fake_kv = _make_mgr()

        req1 = _make_request(model="model-a", messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ])
        req2 = _make_request(model="model-b", messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ])

        r1 = mgr.execute_ephemeral(req1, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        r2 = mgr.execute_ephemeral(req2, backend="fake", generate_fn=_generate_fn)
        assert r2.cache_hit is False  # Different model → miss


# ── Scope enforcement ──────────────────────────────────────────────────────


class TestScopeEnforcement:
    def test_different_thread_scope_misses(self):
        mgr, fake_kv = _make_mgr()
        req1 = _make_request(thread_id="thread-a", messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ])
        req2 = _make_request(thread_id="thread-b", messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ])

        r1 = mgr.execute_ephemeral(req1, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        r2 = mgr.execute_ephemeral(req2, backend="fake", generate_fn=_generate_fn)
        assert r2.cache_hit is False  # Different thread → miss

    def test_same_thread_scope_hits(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request(thread_id="thread-a", messages=[
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ])

        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        r2 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        assert r2.cache_hit is True  # Same thread → hit


# ── Unsupported backend falls through ──────────────────────────────────────


class TestUnsupportedBackend:
    def test_unsupported_backend_uses_full_generation(self):
        mgr, fake_kv = _make_mgr()
        # Register a no-op backend instead
        registry = BackendKVAdapterRegistry()
        mgr2 = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )

        req = _make_request()

        r1 = mgr2.execute_ephemeral(req, backend="stub", generate_fn=_generate_fn)
        assert r1.cache_hit is False

        r2 = mgr2.execute_ephemeral(req, backend="stub", generate_fn=_generate_fn)
        # Should still miss because backend is unsupported
        assert r2.cache_hit is False

    def test_unsupported_backend_observation_reports_unsupported(self):
        registry = BackendKVAdapterRegistry()
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=ThreadWakeIndex(max_entries=50),
        )
        req = _make_request()

        result = mgr.execute_ephemeral(req, backend="stub", generate_fn=_generate_fn)
        assert result.observation is not None
        assert result.observation.backend_kv_capability == "unsupported"
        assert result.observation.can_reuse_kv is False


# ── Check metadata in results ──────────────────────────────────────────────


class TestResultMetadata:
    def test_metadata_on_hit(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)  # miss
        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)  # hit

        assert result.metadata is not None
        assert result.metadata.cache_hit is True
        assert result.metadata.matched_tokens > 0
        assert result.metadata.mode == "ephemeral"

    def test_metadata_on_miss(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request()

        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)

        assert result.metadata is not None
        assert result.metadata.cache_hit is False
        assert result.metadata.matched_tokens == 0

    def test_no_raw_prompt_in_metadata(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request(messages=[
            {"role": "system", "content": "SECRET_PROMPT_DO_NOT_LEAK"},
            {"role": "user", "content": "hello"},
        ])

        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        # Check metadata doesn't contain raw content
        meta_json = result.metadata.model_dump_json() if result.metadata else ""
        assert "SECRET_PROMPT_DO_NOT_LEAK" not in meta_json


# ── Observe mode preserves old behavior ───────────────────────────────────


class TestObserveModeNotReusing:
    def test_observe_mode_request_does_not_use_kv(self):
        mgr, fake_kv = _make_mgr()
        req = _make_request(threadwake_config={
            "enabled": True,
            "mode": "observe",
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        })

        result = mgr.execute_ephemeral(req, backend="fake", generate_fn=_generate_fn)
        assert result.cache_hit is False
        assert result.observation is not None
        # Observe mode with resumable backend now shows can_reuse_kv based on mode
        # In observe mode, can_reuse_kv should be False
        assert result.observation.can_reuse_kv is False

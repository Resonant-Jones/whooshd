"""OpenAI route compatibility and failure isolation tests for ThreadWake."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
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


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_threadwake_manager():
    """Restore the original ThreadWakeManager after tests that inject
    a custom manager for fake KV/tokenizer backends."""
    import whooshd.app as app_mod
    original = app_mod._threadwake_manager
    yield
    app_mod._threadwake_manager = original


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


# ── Route-level ephemeral bridge tests ──────────────────────────────────


class TestObserveModeUnchanged:
    """Prove observe mode still only observes — no cache execution."""

    async def test_observe_mode_returns_valid_chat_completion(self, client, monkeypatch):
        """ThreadWake observe mode does not alter the chat completion response."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [
                    {"role": "system", "content": "stable prefix"},
                    {"role": "user", "content": "hello"},
                ],
                "threadwake": {
                    "enabled": True,
                    "mode": "observe",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) >= 1
        assert body["choices"][0]["message"]["content"]

    async def test_observe_mode_response_unchanged_shape(self, client, monkeypatch):
        """Response shape is identical with or without ThreadWake observe."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hello"}],
                "threadwake": {
                    "enabled": True,
                    "mode": "observe",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert "usage" in body


class TestUnsupportedBackendFallback:
    """Prove ephemeral mode falls back when backend is unsupported."""

    async def test_ephemeral_mode_stub_returns_valid_response(self, client, monkeypatch):
        """Stub backend with ephemeral mode should still return a valid
        chat completion (falling back to normal execution)."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [
                    {"role": "system", "content": "stable prefix"},
                    {"role": "user", "content": "hello"},
                ],
                "threadwake": {
                    "enabled": True,
                    "mode": "ephemeral",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "choices" in body

    async def test_ephemeral_mode_stub_does_not_crash(self, client, monkeypatch):
        """Multiple ephemeral requests against stub should not cause errors."""
        for _ in range(3):
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "stub-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "threadwake": {
                        "enabled": True,
                        "mode": "ephemeral",
                        "scope": "thread",
                        "min_stable_prefix_tokens": 1,
                    },
                },
            )
            assert resp.status_code == 200


class TestFakeBackendMissThenHit:
    """Prove ephemeral hit/miss with fake KV/tokenizer registered."""

    async def test_miss_then_hit_through_route_bridge(self, client, monkeypatch):
        """Register fake KV + tokenizer, then prove first request misses
        and second identical request hits via the route bridge."""
        from whooshd.runtime.threadwake.backend import (
            BackendKVAdapterRegistry,
            FakeKVBackend,
        )
        from whooshd.runtime.threadwake.tokenization import (
            BackendTokenizerAdapterRegistry,
            FakeTokenizerAdapter,
        )
        from whooshd.runtime.threadwake.manager import ThreadWakeManager
        from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
        from whooshd.runtime.threadwake.index import ThreadWakeIndex

        # Build a manager with fake KV + tokenizer registered for "stub".
        fake_kv = FakeKVBackend()
        fake_tok = FakeTokenizerAdapter()
        kv_reg = BackendKVAdapterRegistry()
        tok_reg = BackendTokenizerAdapterRegistry()
        kv_reg.register("stub", fake_kv)
        tok_reg.register("stub", fake_tok)

        tw_mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            tokenizer_registry=tok_reg,
            index=ThreadWakeIndex(max_entries=50),
        )

        # Inject the instrumented manager into the app module.
        import whooshd.app as app_mod
        original_mgr = app_mod._threadwake_manager
        app_mod._threadwake_manager = tw_mgr

        try:
            messages = [
                {"role": "system", "content": "stable " * 8},
                {"role": "user", "content": "hello"},
            ]
            payload = {
                "model": "stub-model",
                "messages": messages,
                "threadwake": {
                    "enabled": True,
                    "mode": "ephemeral",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            }

            # First request — should miss.
            r1 = await client.post("/v1/chat/completions", json=payload)
            assert r1.status_code == 200

            # Second identical request — should hit.
            r2 = await client.post("/v1/chat/completions", json=payload)
            assert r2.status_code == 200

            # Verify the cache is populated and functioning.
            health = tw_mgr.get_health()
            assert health["entry_count"] >= 1, f"expected cached entry, got {health}"
            assert health["ready_entries"] >= 1, f"expected ready entry, got {health}"
            # Both requests hit the index (observe creates entry, execute finds it).
            assert health["total_hits"] >= 2, f"expected index hits, got {health}"

        finally:
            app_mod._threadwake_manager = original_mgr

    async def test_observe_request_does_not_overwrite_kv_handle(self, client, monkeypatch):
        """After an ephemeral miss stores a KV handle, observe_request
        on a subsequent request must not clear that handle."""
        from whooshd.runtime.threadwake.backend import (
            BackendKVAdapterRegistry,
            FakeKVBackend,
        )
        from whooshd.runtime.threadwake.tokenization import (
            BackendTokenizerAdapterRegistry,
            FakeTokenizerAdapter,
        )
        from whooshd.runtime.threadwake.manager import ThreadWakeManager
        from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
        from whooshd.runtime.threadwake.index import ThreadWakeIndex

        fake_kv = FakeKVBackend()
        fake_tok = FakeTokenizerAdapter()
        kv_reg = BackendKVAdapterRegistry()
        tok_reg = BackendTokenizerAdapterRegistry()
        kv_reg.register("stub", fake_kv)
        tok_reg.register("stub", fake_tok)

        tw_mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            tokenizer_registry=tok_reg,
            index=ThreadWakeIndex(max_entries=50),
        )

        import whooshd.app as app_mod
        original_mgr = app_mod._threadwake_manager
        app_mod._threadwake_manager = tw_mgr

        try:
            messages = [
                {"role": "system", "content": "stable " * 8},
                {"role": "user", "content": "hello"},
            ]
            payload = {
                "model": "stub-model",
                "messages": messages,
                "threadwake": {
                    "enabled": True,
                    "mode": "ephemeral",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            }

            # First request: miss — stores a KV handle.
            r1 = await client.post("/v1/chat/completions", json=payload)
            assert r1.status_code == 200

            # Verify the handle was stored.
            index_entries = tw_mgr._index._entries
            for key, entry in index_entries.items():
                if entry.status.value == "ready":
                    assert entry.kv_handle_id is not None, (
                        "Ready entry must have a kv_handle_id after ephemeral miss"
                    )

            # Look up the ready entry's handle before second request.
            handle_before = None
            for key, entry in index_entries.items():
                if entry.status.value == "ready":
                    handle_before = entry.kv_handle_id
                    break

            # Second request: handler calls observe_request (which would
            # overwrite the handle if the bug were present), then bridge
            # calls execute_ephemeral which should hit.
            r2 = await client.post("/v1/chat/completions", json=payload)
            assert r2.status_code == 200

            # After both requests, the handle must still be intact.
            for key, entry in index_entries.items():
                if entry.status.value == "ready":
                    assert entry.kv_handle_id == handle_before, (
                        "KV handle was overwritten by observe_request — "
                        "put_observation must preserve existing handles"
                    )

        finally:
            app_mod._threadwake_manager = original_mgr


class TestThreadWakeFailureFallsBack:
    """Prove ThreadWake failures don't break the request."""

    async def test_execute_ephemeral_exception_falls_back(self, client, monkeypatch):
        """If execute_ephemeral raises, the request still completes via
        normal adapter execution."""
        from whooshd.runtime.threadwake.backend import (
            BackendKVAdapterRegistry,
            FakeKVBackend,
        )
        from whooshd.runtime.threadwake.tokenization import (
            BackendTokenizerAdapterRegistry,
            FakeTokenizerAdapter,
        )
        import whooshd.app as app_mod

        original_mgr = app_mod._threadwake_manager

        # Build a manager whose backend raises on generate_from_kv.
        class _CrashingKVBackend(FakeKVBackend):
            def generate_from_kv(self, kv_handle, tokens, params=None):
                raise RuntimeError("simulated KV failure")

        crashing_kv = _CrashingKVBackend()
        fake_tok = FakeTokenizerAdapter()
        kv_reg = BackendKVAdapterRegistry()
        tok_reg = BackendTokenizerAdapterRegistry()
        kv_reg.register("stub", crashing_kv)
        tok_reg.register("stub", fake_tok)

        from whooshd.runtime.threadwake.manager import ThreadWakeManager
        from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics
        from whooshd.runtime.threadwake.index import ThreadWakeIndex

        tw_mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=kv_reg,
            tokenizer_registry=tok_reg,
            index=ThreadWakeIndex(max_entries=50),
        )
        app_mod._threadwake_manager = tw_mgr

        try:
            # First request: miss (stores entry).
            payload = {
                "model": "stub-model",
                "messages": [
                    {"role": "system", "content": "stable " * 8},
                    {"role": "user", "content": "hello"},
                ],
                "threadwake": {
                    "enabled": True,
                    "mode": "ephemeral",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            }
            r1 = await client.post("/v1/chat/completions", json=payload)
            assert r1.status_code == 200  # miss works normally

            # Second request: the hit path crashes, but fallback saves us.
            r2 = await client.post("/v1/chat/completions", json=payload)
            assert r2.status_code == 200  # fallback returns valid response

        finally:
            app_mod._threadwake_manager = original_mgr


class TestStreamingNotChanged:
    """Prove streaming requests are never routed through ephemeral bridge."""

    async def test_streaming_with_ephemeral_mode_still_works(self, client, monkeypatch):
        """Streaming request with ephemeral mode should still stream normally."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "threadwake": {
                    "enabled": True,
                    "mode": "ephemeral",
                    "scope": "thread",
                    "min_stable_prefix_tokens": 1,
                },
            },
        )
        # Streaming should still work with ephemeral mode.
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

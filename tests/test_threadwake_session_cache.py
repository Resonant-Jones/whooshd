"""Tests for ThreadWake session continuation cache — monotonic append, thread tips, cross-thread isolation."""

from __future__ import annotations

from whooshd.contracts import ChatCompletionRequest
from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry, FakeKVBackend
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex
from whooshd.runtime.threadwake.tokenization import BackendTokenizerAdapterRegistry, FakeTokenizerAdapter
from whooshd.runtime.threadwake.manager import ThreadWakeManager
from whooshd.runtime.threadwake.metrics import ThreadWakeMetrics


def _make_request(messages=None, thread_id=None, mode="ephemeral"):
    if messages is None:
        messages = [
            {"role": "system", "content": "stable " * 8},
            {"role": "user", "content": "hello"},
        ]
    data = {
        "model": "test-model",
        "messages": messages,
        "threadwake": {
            "enabled": True,
            "mode": mode,
            "scope": "thread",
            "min_stable_prefix_tokens": 1,
        },
    }
    if thread_id:
        data["thread_id"] = thread_id
    return ChatCompletionRequest.model_validate(data)


def _make_mgr():
    fake_kv = FakeKVBackend()
    fake_tok = FakeTokenizerAdapter()
    registry = BackendKVAdapterRegistry()
    tok_registry = BackendTokenizerAdapterRegistry()
    registry.register("fake", fake_kv)
    tok_registry.register("fake", fake_tok)
    index = ThreadWakeIndex(max_entries=50)
    mgr = ThreadWakeManager(
        metrics=ThreadWakeMetrics(),
        backend_registry=registry,
        tokenizer_registry=tok_registry,
        index=index,
    )
    return mgr, fake_kv, index


def _gen(request, params):
    return [f"gen_{i}" for i in range(params.get("max_tokens", 4))]


# ── Basic session continuation ─────────────────────────────────────────────


class TestSessionContinuation:
    def test_turn1_stores_thread_tip(self):
        mgr, fake_kv, index = _make_mgr()
        req = _make_request(
            messages=[
                {"role": "system", "content": "System prompt " * 8},
                {"role": "user", "content": "Turn 1 query"},
            ],
            thread_id="session-t1",
        )

        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False

        # Thread tip should be stored
        tip = index.get_latest_for_thread("session-t1", "test-model", "fake")
        assert tip is not None
        assert tip.segment_count > 0

    def test_turn2_appends_and_hits_continuation(self):
        mgr, fake_kv, index = _make_mgr()

        # Turn 1: system + user
        turn1 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
            ],
            thread_id="session-t2",
        )
        r1 = mgr.execute_ephemeral(turn1, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False

        # Turn 2: same system + user + assistant + new user
        turn2 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
            ],
            thread_id="session-t2",
        )
        r2 = mgr.execute_ephemeral(turn2, backend="fake", generate_fn=_gen)
        assert r2.cache_hit is True
        assert r2.matched_tokens > 0

    def test_turn3_further_appends_and_hits(self):
        mgr, fake_kv, index = _make_mgr()

        turn1 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
            ],
            thread_id="session-t3",
        )
        mgr.execute_ephemeral(turn1, backend="fake", generate_fn=_gen)

        turn2 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
            ],
            thread_id="session-t3",
        )
        mgr.execute_ephemeral(turn2, backend="fake", generate_fn=_gen)

        turn3 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
                {"role": "assistant", "content": "Response 2"},
                {"role": "user", "content": "Turn 3"},
            ],
            thread_id="session-t3",
        )
        r3 = mgr.execute_ephemeral(turn3, backend="fake", generate_fn=_gen)
        assert r3.cache_hit is True


# ── Edited/missing history misses ──────────────────────────────────────────


class TestEditedHistory:
    def test_edited_prior_message_misses(self):
        mgr, fake_kv, index = _make_mgr()

        # Turn 1: system A + user
        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System A " * 8},
                    {"role": "user", "content": "Turn 1"},
                ],
                thread_id="session-edit",
            ),
            backend="fake", generate_fn=_gen,
        )

        # Turn 2: changed system prompt — non-monotonic
        r2 = mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System B " * 8},  # Edited!
                    {"role": "user", "content": "Turn 1"},
                    {"role": "assistant", "content": "Response 1"},
                ],
                thread_id="session-edit",
            ),
            backend="fake", generate_fn=_gen,
        )
        assert r2.cache_hit is False  # Should miss due to edit
        # Observation should have the right reason
        if r2.observation:
            assert r2.observation.kv_reuse_reason in (
                "non_monotonic_or_changed_prefix", None,
            )

    def test_changed_system_prompt_misses(self):
        mgr, fake_kv, index = _make_mgr()

        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System original " * 8},
                    {"role": "user", "content": "Hello"},
                ],
                thread_id="session-syschange",
            ),
            backend="fake", generate_fn=_gen,
        )

        r2 = mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System changed " * 8},  # Different!
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Response"},
                ],
                thread_id="session-syschange",
            ),
            backend="fake", generate_fn=_gen,
        )
        assert r2.cache_hit is False


# ── Cross-thread isolation ─────────────────────────────────────────────────


class TestCrossThreadIsolation:
    def test_different_thread_id_misses(self):
        mgr, fake_kv, index = _make_mgr()

        # Thread A
        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Hello A"},
                ],
                thread_id="thread-a",
            ),
            backend="fake", generate_fn=_gen,
        )

        # Thread B — same messages but different thread
        r2 = mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Hello B"},
                ],
                thread_id="thread-b",
            ),
            backend="fake", generate_fn=_gen,
        )
        assert r2.cache_hit is False  # Different thread → miss

    def test_thread_a_tip_not_visible_to_thread_b(self):
        mgr, fake_kv, index = _make_mgr()

        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Turn 1"},
                ],
                thread_id="thread-a",
            ),
            backend="fake", generate_fn=_gen,
        )

        # Thread B should have no tip
        tip = index.get_latest_for_thread("thread-b", "test-model", "fake")
        assert tip is None

        # Thread A should have a tip
        tip = index.get_latest_for_thread("thread-a", "test-model", "fake")
        assert tip is not None


# ── Missing thread_id ──────────────────────────────────────────────────────


class TestMissingThreadId:
    def test_no_thread_id_does_not_use_session_cache(self):
        mgr, fake_kv, index = _make_mgr()

        # First request without thread_id
        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Hello"},
                ],
                thread_id=None,
            ),
            backend="fake", generate_fn=_gen,
        )

        # Should not have stored a thread tip
        assert index.thread_tip_count() == 0  # No tip without thread_id


# ── Fallback path ──────────────────────────────────────────────────────────


class TestFallbackPath:
    def test_full_generation_path_still_works(self):
        """Even when session continuation isn't possible, generation succeeds."""
        mgr, fake_kv, index = _make_mgr()

        # Request without thread_id — no session possible, but generation works
        req = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
            thread_id=None,
        )
        r1 = mgr.execute_ephemeral(req, backend="fake", generate_fn=_gen)
        assert r1.cache_hit is False
        assert len(r1.output_tokens) > 0  # Generation succeeded

    def test_backend_clone_failure_falls_through(self):
        """When clone fails in session continuation, fall through to ephemeral."""
        # Create a backend where clone_kv raises after first use
        class FailingCloneAfterFirst:
            def __init__(self):
                self.calls = 0

            def supports_kv_cache(self):
                from whooshd.runtime.threadwake.handles import KVCapability
                return KVCapability.RESUMABLE

            def prefill_to_kv(self, tokens, *, model_id, metadata=None):
                from whooshd.runtime.threadwake.handles import KVHandle
                return KVHandle(backend="failing", model_id=model_id,
                                token_count=len(tokens), opaque_ref={"tokens": tokens})

            def generate_from_kv(self, kv_handle, new_tokens, generation_params):
                yield "gen_0"

            def clone_kv(self, kv_handle):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("clone fails after first call")
                return kv_handle

            def release_kv(self, kv_handle):
                pass

        from whooshd.runtime.threadwake.backend import BackendKVAdapterRegistry
        from whooshd.runtime.threadwake.index import ThreadWakeIndex
        registry = BackendKVAdapterRegistry()
        backend = FailingCloneAfterFirst()
        registry.register("failing", backend)
        index = ThreadWakeIndex(max_entries=50)
        mgr = ThreadWakeManager(
            metrics=ThreadWakeMetrics(),
            backend_registry=registry,
            index=index,
        )

        turn1 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
            ],
            thread_id="session-failclone",
        )
        mgr.execute_ephemeral(turn1, backend="failing", generate_fn=_gen)

        turn2 = _make_request(
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response"},
                {"role": "user", "content": "Turn 2"},
            ],
            thread_id="session-failclone",
        )
        r2 = mgr.execute_ephemeral(turn2, backend="failing", generate_fn=_gen)
        # Should fall through to ephemeral path and produce output
        assert len(r2.output_tokens) > 0


# ── Thread tip lifecycle ──────────────────────────────────────────────────


class TestThreadTipLifecycle:
    def test_clear_thread_tip_removes_tip(self):
        mgr, fake_kv, index = _make_mgr()

        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Hello"},
                ],
                thread_id="session-clear",
            ),
            backend="fake", generate_fn=_gen,
        )

        assert index.get_latest_for_thread("session-clear", "test-model", "fake") is not None

        index.clear_thread_tip("session-clear", "test-model", "fake")
        assert index.get_latest_for_thread("session-clear", "test-model", "fake") is None

    def test_non_monotonic_resets_thread_tip(self):
        mgr, fake_kv, index = _make_mgr()

        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System " * 8},
                    {"role": "user", "content": "Turn 1"},
                ],
                thread_id="session-nonmono",
            ),
            backend="fake", generate_fn=_gen,
        )

        old_tip = index.get_latest_for_thread("session-nonmono", "test-model", "fake")
        old_chain = old_tip.chain_hash if old_tip else None

        # Create a non-monotonic request (edited system prompt)
        mgr.execute_ephemeral(
            _make_request(
                messages=[
                    {"role": "system", "content": "System edited " * 8},
                    {"role": "user", "content": "Turn 1"},
                    {"role": "assistant", "content": "Response"},
                ],
                thread_id="session-nonmono",
            ),
            backend="fake", generate_fn=_gen,
        )

        # Old tip should be replaced with new one (non-monotonic → fallback → new tip)
        new_tip = index.get_latest_for_thread("session-nonmono", "test-model", "fake")
        assert new_tip is not None
        assert new_tip.chain_hash != old_chain  # Chain hash changed

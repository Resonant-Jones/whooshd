"""Tests for ThreadWake chain hashing and monotonic append validation."""

from __future__ import annotations

from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.index import ThreadTip, ThreadWakeIndex
from whooshd.runtime.threadwake.keys import sha256_hex


class TestChainHash:
    def test_prompt_graph_has_chain_hash(self):
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System prompt " * 8},
                {"role": "user", "content": "Hello"},
            ],
        )
        assert graph.full_prefix_chain_hash
        assert len(graph.full_prefix_chain_hash) == 64  # SHA-256 hex
        assert graph.ordered_segment_hashes
        assert len(graph.ordered_segment_hashes) == 2  # system + user

    def test_chain_hash_changes_with_different_messages(self):
        g1 = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System A " * 8},
                {"role": "user", "content": "Hello"},
            ],
        )
        g2 = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System B " * 8},
                {"role": "user", "content": "Hello"},
            ],
        )
        assert g1.full_prefix_chain_hash != g2.full_prefix_chain_hash

    def test_same_messages_produce_same_chain_hash(self):
        messages = [
            {"role": "system", "content": "System " * 8},
            {"role": "user", "content": "Hello"},
        ]
        g1 = compile_prompt_graph(model_id="m", backend="stub", messages=messages)
        g2 = compile_prompt_graph(model_id="m", backend="stub", messages=messages)
        assert g1.full_prefix_chain_hash == g2.full_prefix_chain_hash

    def test_ordered_segment_hashes_match_segments(self):
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Hello"},
            ],
        )
        assert len(graph.ordered_segment_hashes) == len(graph.segments)
        for i, seg in enumerate(graph.segments):
            assert graph.ordered_segment_hashes[i] == seg.content_hash

    def test_continuation_candidate_true_with_semi_stable(self):
        """Session continuations have semi-stable segments (assistant msgs)."""
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System " * 8},
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
            ],
        )
        # Has semi-stable segments → continuation candidate
        assert graph.continuation_candidate is True

    def test_any_non_empty_graph_is_continuation_candidate(self):
        graph = compile_prompt_graph(
            model_id="m", backend="stub",
            messages=[
                {"role": "system", "content": "System " * 8},
            ],
        )
        # Any non-empty graph can be continued
        assert graph.continuation_candidate is True


class TestMonotonicAppendValidation:
    def test_exact_append_is_valid(self):
        index = ThreadWakeIndex()
        prev = ["hash_a", "hash_b", "hash_c"]
        new = ["hash_a", "hash_b", "hash_c", "hash_d", "hash_e"]
        assert index.validate_monotonic_append(prev, new) is True

    def test_single_new_segment_is_valid(self):
        index = ThreadWakeIndex()
        prev = ["hash_a"]
        new = ["hash_a", "hash_b"]
        assert index.validate_monotonic_append(prev, new) is True

    def test_edited_middle_is_invalid(self):
        index = ThreadWakeIndex()
        prev = ["hash_a", "hash_b", "hash_c"]
        new = ["hash_a", "hash_x", "hash_c", "hash_d"]  # hash_b edited
        assert index.validate_monotonic_append(prev, new) is False

    def test_truncation_is_invalid(self):
        index = ThreadWakeIndex()
        prev = ["hash_a", "hash_b", "hash_c"]
        new = ["hash_a"]  # Truncated
        assert index.validate_monotonic_append(prev, new) is False

    def test_empty_previous_is_valid(self):
        index = ThreadWakeIndex()
        prev: list[str] = []
        new = ["hash_a", "hash_b"]
        assert index.validate_monotonic_append(prev, new) is True

    def test_reordered_is_invalid(self):
        index = ThreadWakeIndex()
        prev = ["hash_a", "hash_b"]
        new = ["hash_b", "hash_a", "hash_c"]  # Reordered
        assert index.validate_monotonic_append(prev, new) is False


class TestThreadTipStorage:
    def test_store_and_retrieve_tip(self):
        index = ThreadWakeIndex()
        index.store_thread_tip(
            thread_id="t1", model_id="m", backend="fake",
            chain_hash="abc123",
            ordered_segment_hashes=["h1", "h2", "h3"],
            kv_handle_id="kv-001",
        )

        tip = index.get_latest_for_thread("t1", "m", "fake")
        assert tip is not None
        assert tip.chain_hash == "abc123"
        assert tip.segment_count == 3
        assert tip.kv_handle_id == "kv-001"

    def test_different_model_isolation(self):
        index = ThreadWakeIndex()
        index.store_thread_tip(
            thread_id="t1", model_id="model-a", backend="fake",
            chain_hash="abc", ordered_segment_hashes=["h1"],
        )
        # Same thread, different model
        tip = index.get_latest_for_thread("t1", "model-b", "fake")
        assert tip is None

    def test_different_backend_isolation(self):
        index = ThreadWakeIndex()
        index.store_thread_tip(
            thread_id="t1", model_id="m", backend="fake",
            chain_hash="abc", ordered_segment_hashes=["h1"],
        )
        tip = index.get_latest_for_thread("t1", "m", "other_backend")
        assert tip is None

    def test_update_existing_tip(self):
        index = ThreadWakeIndex()
        index.store_thread_tip(
            thread_id="t1", model_id="m", backend="fake",
            chain_hash="old_chain", ordered_segment_hashes=["h1"],
        )
        index.store_thread_tip(
            thread_id="t1", model_id="m", backend="fake",
            chain_hash="new_chain", ordered_segment_hashes=["h1", "h2"],
        )

        tip = index.get_latest_for_thread("t1", "m", "fake")
        assert tip.chain_hash == "new_chain"
        assert tip.segment_count == 2

    def test_thread_id_privacy(self):
        """ThreadTip stores hashed thread_id, not raw."""
        index = ThreadWakeIndex()
        index.store_thread_tip(
            thread_id="sensitive-thread-12345", model_id="m", backend="fake",
            chain_hash="abc", ordered_segment_hashes=["h1"],
        )
        tip = index.get_latest_for_thread("sensitive-thread-12345", "m", "fake")
        assert tip is not None
        # thread_id_hash should be SHA-256, not raw
        assert tip.thread_id_hash == sha256_hex("sensitive-thread-12345")
        assert tip.thread_id_hash != "sensitive-thread-12345"


class TestRealisticSessionFlow:
    def test_five_turn_conversation_chain(self):
        """Simulate a 5-turn conversation and verify chain hashes."""
        system = {"role": "system", "content": "System " * 8}
        messages: list[dict] = [system]

        chains: list[str] = []
        for turn in range(1, 6):
            messages.append({"role": "user", "content": f"Turn {turn} query"})
            graph = compile_prompt_graph(
                model_id="m", backend="stub", messages=messages,
            )
            chains.append(graph.full_prefix_chain_hash)
            messages.append({"role": "assistant", "content": f"Turn {turn} response"})

        # All chains should be unique (growing conversation)
        assert len(set(chains)) == 5

    def test_chain_validation_across_turns(self):
        """Validation: each turn's chain should be a monotonic append of the previous."""
        index = ThreadWakeIndex()
        system = {"role": "system", "content": "System " * 8}
        messages: list[dict] = [system]

        previous_hashes: list[str] = []
        for turn in range(1, 4):
            messages.append({"role": "user", "content": f"Turn {turn} query"})
            graph = compile_prompt_graph(
                model_id="m", backend="stub", messages=messages,
            )

            if previous_hashes:
                assert index.validate_monotonic_append(
                    previous_hashes, graph.ordered_segment_hashes,
                ) is True

            previous_hashes = list(graph.ordered_segment_hashes)
            messages.append({"role": "assistant", "content": f"Turn {turn} response"})

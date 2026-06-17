"""Tests for stable prefix proof engine."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.prefix_proof import (
    PrefixMismatchReason,
    PrefixProof,
    StablePrefixProofEngine,
)


class MockTokenizedPrompt:
    def __init__(self, **kwargs):
        self.real_tokenization = kwargs.get("real_tokenization", True)
        self.model_id = kwargs.get("model_id", "test-model")
        self.backend = kwargs.get("backend", "fake")
        self.tokenizer_hash = kwargs.get("tokenizer_hash", "tok-hash-001")
        self.chat_template_hash = kwargs.get("chat_template_hash", "tmpl-hash-001")
        self.token_ids = kwargs.get("token_ids", [1, 2, 3, 4, 5])


# ── Compatible proofs ─────────────────────────────────────────────────────


class TestCompatible:
    def test_identical_prompts_are_compatible(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        b = MockTokenizedPrompt()
        proof = engine.compare(a, b)
        assert proof.compatible is True
        assert proof.shared_prefix_tokens == 5
        assert proof.prefix_hash is not None
        assert proof.reason is None

    def test_partial_prefix_is_compatible(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[10, 20, 30, 40, 50])
        b = MockTokenizedPrompt(token_ids=[10, 20, 30, 99, 99])
        proof = engine.compare(a, b)
        assert proof.compatible is True
        assert proof.shared_prefix_tokens == 3
        assert proof.divergence_index == 3

    def test_full_prefix_no_divergence(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[1, 2, 3])
        b = MockTokenizedPrompt(token_ids=[1, 2, 3])
        proof = engine.compare(a, b)
        assert proof.compatible is True
        assert proof.shared_prefix_tokens == 3
        assert proof.divergence_index is None  # Identical length, no divergence

    def test_prefix_hash_is_deterministic(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[7, 8, 9])
        b = MockTokenizedPrompt(token_ids=[7, 8, 9])
        p1 = engine.compare(a, b)
        p2 = engine.compare(a, b)
        assert p1.prefix_hash == p2.prefix_hash


# ── Mismatch reasons ──────────────────────────────────────────────────────


class TestMismatches:
    def test_no_real_tokenization_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(real_tokenization=False)
        b = MockTokenizedPrompt()
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.REAL_TOKENIZATION_UNAVAILABLE.value

    def test_model_mismatch_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(model_id="model-a")
        b = MockTokenizedPrompt(model_id="model-b")
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.MODEL_CHANGED.value

    def test_backend_mismatch_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(backend="mlx")
        b = MockTokenizedPrompt(backend="llama_cpp")
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.BACKEND_CHANGED.value

    def test_tokenizer_mismatch_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(tokenizer_hash="hash-a")
        b = MockTokenizedPrompt(tokenizer_hash="hash-b")
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.TOKENIZER_CHANGED.value

    def test_chat_template_mismatch_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(chat_template_hash="tmpl-a")
        b = MockTokenizedPrompt(chat_template_hash="tmpl-b")
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.CHAT_TEMPLATE_CHANGED.value

    def test_divergent_first_token_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[1, 2, 3])
        b = MockTokenizedPrompt(token_ids=[9, 2, 3])
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.divergence_index == 0
        assert proof.reason == PrefixMismatchReason.TOKEN_SEQUENCE_CHANGED.value

    def test_empty_prompt_rejects(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[])
        b = MockTokenizedPrompt(token_ids=[1, 2, 3])
        proof = engine.compare(a, b)
        assert proof.compatible is False
        assert proof.reason == PrefixMismatchReason.EMPTY_PROMPT.value


# ── Proof serialization ───────────────────────────────────────────────────


class TestProofSerialization:
    def test_proof_contains_no_raw_token_ids(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(token_ids=[101, 202, 303])
        b = MockTokenizedPrompt(token_ids=[101, 202, 303])
        proof = engine.compare(a, b)
        d = proof.safe_dict()
        assert "token_ids" not in d
        assert "101" not in json.dumps(d)

    def test_proof_is_json_serializable(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        proof = engine.compare(a, a)
        json.dumps(proof.safe_dict())  # Should not raise

    def test_proof_contains_metadata(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        proof = engine.compare(a, a)
        d = proof.safe_dict()
        assert d["backend"] == "fake"
        assert d["model_id"] == "test-model"
        assert d["tokenizer_hash"] == "tok-hash-001"


# ── Engine stats ──────────────────────────────────────────────────────────


class TestEngineStats:
    def test_stats_track_comparisons(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        engine.compare(a, a)
        engine.compare(a, a)
        s = engine.stats()
        assert s["comparisons"] == 2
        assert s["matches"] == 2
        assert s["mismatches"] == 0

    def test_stats_track_mismatches(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt(model_id="a")
        b = MockTokenizedPrompt(model_id="b")
        engine.compare(a, b)
        s = engine.stats()
        assert s["mismatches"] == 1
        assert s["matches"] == 0

    def test_reset_clears_stats(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        engine.compare(a, a)
        engine.reset()
        s = engine.stats()
        assert s["comparisons"] == 0

    def test_stats_output_safe(self):
        engine = StablePrefixProofEngine()
        a = MockTokenizedPrompt()
        engine.compare(a, a)
        s = engine.stats()
        assert "token_ids" not in s
        assert isinstance(s["comparisons"], int)


# ── shared_prefix_length ──────────────────────────────────────────────────


class TestSharedPrefixLength:
    def test_exact_match(self):
        n = StablePrefixProofEngine.shared_prefix_length([1, 2, 3], [1, 2, 3])
        assert n == 3

    def test_partial_match(self):
        n = StablePrefixProofEngine.shared_prefix_length([1, 2, 3], [1, 2, 9])
        assert n == 2

    def test_no_match(self):
        n = StablePrefixProofEngine.shared_prefix_length([1, 2], [9, 8])
        assert n == 0

    def test_empty(self):
        n = StablePrefixProofEngine.shared_prefix_length([], [1, 2])
        assert n == 0

    def test_one_empty(self):
        n = StablePrefixProofEngine.shared_prefix_length([1], [])
        assert n == 0

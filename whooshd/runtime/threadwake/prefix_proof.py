"""Stable prefix proof engine for ThreadWake Phase M7.

Determines whether two tokenized prompts share an identical reusable
prefix — without performing any KV reuse.  Outputs proof metadata only.

The engine enforces strict exact-token equality.  No fuzzy matching,
no semantic similarity, no text-level comparison.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Mismatch reason ────────────────────────────────────────────────────────


class PrefixMismatchReason(str, Enum):
    """Why a prefix comparison did not result in a compatible proof."""

    REAL_TOKENIZATION_UNAVAILABLE = "real_tokenization_unavailable"
    TOKENIZER_CHANGED = "tokenizer_changed"
    CHAT_TEMPLATE_CHANGED = "chat_template_changed"
    MODEL_CHANGED = "model_changed"
    BACKEND_CHANGED = "backend_changed"
    TOKEN_SEQUENCE_CHANGED = "token_sequence_changed"
    EMPTY_PROMPT = "empty_prompt"
    UNSUPPORTED_BACKEND = "unsupported_backend"


# ── Prefix proof ───────────────────────────────────────────────────────────


@dataclass
class PrefixProof:
    """Result of comparing two tokenized prompts for prefix compatibility.

    All fields are safe for external consumption — no raw token IDs
    or prompt text are stored.
    """

    compatible: bool = False
    shared_prefix_tokens: int = 0
    divergence_index: int | None = None
    prefix_hash: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    model_id: str | None = None
    backend: str | None = None
    reason: str | None = None

    def safe_dict(self) -> dict:
        return {
            "compatible": self.compatible,
            "shared_prefix_tokens": self.shared_prefix_tokens,
            "divergence_index": self.divergence_index,
            "prefix_hash": self.prefix_hash,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "model_id": self.model_id,
            "backend": self.backend,
            "reason": self.reason,
        }


# ── Engine ─────────────────────────────────────────────────────────────────


@dataclass
class _EngineStats:
    comparisons: int = 0
    matches: int = 0
    mismatches: int = 0

    def to_dict(self) -> dict:
        return {
            "comparisons": self.comparisons,
            "matches": self.matches,
            "mismatches": self.mismatches,
        }


class StablePrefixProofEngine:
    """Compares two TokenizedPrompts for exact prefix compatibility.

    Produces a ``PrefixProof`` indicating whether the prompts share
    an identical token prefix suitable for KV reuse.  The engine is
    a pure function — no side effects, no KV mutations.
    """

    def __init__(self) -> None:
        self._stats = _EngineStats()

    # ── Public API ───────────────────────────────────────────────────────

    def compare(self, a: object, b: object) -> PrefixProof:
        """Compare two TokenizedPrompts and return a PrefixProof."""
        self._stats.comparisons += 1

        # Guard: both must have real tokenization
        a_real = getattr(a, "real_tokenization", False)
        b_real = getattr(b, "real_tokenization", False)
        if not a_real or not b_real:
            result = PrefixProof(
                compatible=False,
                reason=PrefixMismatchReason.REAL_TOKENIZATION_UNAVAILABLE.value,
            )
            self._stats.mismatches += 1
            return result

        # Guard: metadata must match
        a_model = getattr(a, "model_id", "") or ""
        b_model = getattr(b, "model_id", "") or ""
        if a_model != b_model:
            result = PrefixProof(
                compatible=False,
                model_id=a_model,
                reason=PrefixMismatchReason.MODEL_CHANGED.value,
            )
            self._stats.mismatches += 1
            return result

        a_backend = getattr(a, "backend", "") or ""
        b_backend = getattr(b, "backend", "") or ""
        if a_backend != b_backend:
            result = PrefixProof(
                compatible=False,
                backend=a_backend,
                reason=PrefixMismatchReason.BACKEND_CHANGED.value,
            )
            self._stats.mismatches += 1
            return result

        a_tok_hash = getattr(a, "tokenizer_hash", None)
        b_tok_hash = getattr(b, "tokenizer_hash", None)
        if a_tok_hash != b_tok_hash:
            result = PrefixProof(
                compatible=False,
                tokenizer_hash=a_tok_hash,
                reason=PrefixMismatchReason.TOKENIZER_CHANGED.value,
            )
            self._stats.mismatches += 1
            return result

        a_tmpl_hash = getattr(a, "chat_template_hash", None)
        b_tmpl_hash = getattr(b, "chat_template_hash", None)
        if a_tmpl_hash != b_tmpl_hash:
            result = PrefixProof(
                compatible=False,
                chat_template_hash=a_tmpl_hash,
                reason=PrefixMismatchReason.CHAT_TEMPLATE_CHANGED.value,
            )
            self._stats.mismatches += 1
            return result

        # Guard: token sequences must be non-empty
        a_tokens: list[int] = list(getattr(a, "token_ids", []) or [])
        b_tokens: list[int] = list(getattr(b, "token_ids", []) or [])
        if not a_tokens or not b_tokens:
            result = PrefixProof(
                compatible=False,
                reason=PrefixMismatchReason.EMPTY_PROMPT.value,
            )
            self._stats.mismatches += 1
            return result

        # Find exact shared prefix length
        shared = self.shared_prefix_length(a_tokens, b_tokens)
        if shared == 0:
            result = PrefixProof(
                compatible=False,
                divergence_index=0,
                reason=PrefixMismatchReason.TOKEN_SEQUENCE_CHANGED.value,
                model_id=a_model,
                backend=a_backend,
                tokenizer_hash=a_tok_hash,
                chat_template_hash=a_tmpl_hash,
            )
            self._stats.mismatches += 1
            return result

        # Compute prefix hash from the shared token slice
        prefix_hash = self.hash_prefix(a_tokens[:shared])

        result = PrefixProof(
            compatible=True,
            shared_prefix_tokens=shared,
            divergence_index=shared if shared < max(len(a_tokens), len(b_tokens)) else None,
            prefix_hash=prefix_hash,
            tokenizer_hash=a_tok_hash,
            chat_template_hash=a_tmpl_hash,
            model_id=a_model,
            backend=a_backend,
            reason=None,
        )
        self._stats.matches += 1
        return result

    @staticmethod
    def shared_prefix_length(a_tokens: list[int], b_tokens: list[int]) -> int:
        """Return the number of leading token IDs that are exactly equal."""
        n = 0
        for x, y in zip(a_tokens, b_tokens):
            if x != y:
                break
            n += 1
        return n

    @staticmethod
    def hash_prefix(token_ids: list[int]) -> str:
        """Return a deterministic SHA-256 hash of a token ID sequence."""
        payload = ",".join(str(t) for t in token_ids)
        return hashlib.sha256(payload.encode()).hexdigest()

    def stats(self) -> dict:
        return self._stats.to_dict()

    def reset(self) -> None:
        self._stats = _EngineStats()

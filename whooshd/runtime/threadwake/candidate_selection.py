"""Candidate KV snapshot selection for ThreadWake Phase M8.

Uses PrefixProof results to identify and score hypothetical reusable
KV snapshot candidates.  Produces metadata only — no KV state is
created, restored, cloned, or injected.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Selection reason ───────────────────────────────────────────────────────


class CandidateSelectionReason(str, Enum):
    PROOF_COMPATIBLE = "proof_compatible"
    SHARED_PREFIX_BELOW_THRESHOLD = "shared_prefix_below_threshold"
    INCOMPATIBLE_PROOF = "incompatible_proof"
    MISSING_PROOF = "missing_proof"
    EMPTY_PROMPT = "empty_prompt"
    INSUFFICIENT_SAVINGS = "insufficient_savings"
    REAL_TOKENIZATION_UNAVAILABLE = "real_tokenization_unavailable"


# ── Confidence ─────────────────────────────────────────────────────────────


class CandidateConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── Snapshot candidate ─────────────────────────────────────────────────────


@dataclass
class SnapshotCandidate:
    """A hypothetical reusable KV snapshot candidate.

    All fields are safe for external consumption — no raw token IDs,
    prompts, or opaque refs.
    """

    candidate_id: str
    prefix_hash: str
    shared_prefix_tokens: int = 0
    total_tokens: int = 0
    dynamic_tail_tokens: int = 0
    potential_saved_tokens: int = 0
    potential_saved_ratio: float = 0.0
    model_id: str | None = None
    backend: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    compatible: bool = False
    reason: str | None = None

    def safe_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "prefix_hash": self.prefix_hash,
            "shared_prefix_tokens": self.shared_prefix_tokens,
            "total_tokens": self.total_tokens,
            "dynamic_tail_tokens": self.dynamic_tail_tokens,
            "potential_saved_tokens": self.potential_saved_tokens,
            "potential_saved_ratio": round(self.potential_saved_ratio, 4),
            "model_id": self.model_id,
            "backend": self.backend,
            "compatible": self.compatible,
            "reason": self.reason,
        }


# ── Candidate score ────────────────────────────────────────────────────────


@dataclass
class CandidateScore:
    score: float = 0.0
    potential_saved_tokens: int = 0
    potential_saved_ratio: float = 0.0
    confidence: str = CandidateConfidence.LOW.value
    selection_reason: str = ""

    def safe_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "potential_saved_tokens": self.potential_saved_tokens,
            "potential_saved_ratio": round(self.potential_saved_ratio, 4),
            "confidence": self.confidence,
            "selection_reason": self.selection_reason,
        }


# ── Selection result ───────────────────────────────────────────────────────


@dataclass
class SnapshotSelectionResult:
    selected: bool = False
    candidate: SnapshotCandidate | None = None
    score: CandidateScore | None = None
    reason: str | None = None

    def safe_dict(self) -> dict:
        return {
            "selected": self.selected,
            "candidate": self.candidate.safe_dict() if self.candidate else None,
            "score": self.score.safe_dict() if self.score else None,
            "reason": self.reason,
        }


# ── Stats ───────────────────────────────────────────────────────────────────


@dataclass
class _SelectionStats:
    evaluations: int = 0
    selected: int = 0
    rejected: int = 0
    potential_saved_tokens_total: int = 0

    def to_dict(self) -> dict:
        return {
            "evaluations": self.evaluations,
            "selected": self.selected,
            "rejected": self.rejected,
            "potential_saved_tokens_total": self.potential_saved_tokens_total,
        }


# ── Selector ────────────────────────────────────────────────────────────────


class SnapshotCandidateSelector:
    """Evaluates PrefixProof results to select KV snapshot candidates.

    Produces a ``SnapshotSelectionResult`` with a scored candidate
    if the proof is compatible and savings justify selection.
    No KV state is mutated.
    """

    def __init__(self, min_prefix_tokens: int = 256) -> None:
        self._min_prefix_tokens = max(min_prefix_tokens, 1)
        self._stats = _SelectionStats()

    # ── Public ──────────────────────────────────────────────────────────

    def evaluate(
        self,
        proof: object,
        total_tokens: int = 0,
    ) -> SnapshotSelectionResult:
        """Evaluate a PrefixProof for candidate selection."""
        self._stats.evaluations += 1

        compatible = getattr(proof, "compatible", False)
        shared = getattr(proof, "shared_prefix_tokens", 0)
        reason = getattr(proof, "reason", None)

        if proof is None:
            result = SnapshotSelectionResult(
                selected=False,
                reason=CandidateSelectionReason.MISSING_PROOF.value,
            )
            self._stats.rejected += 1
            return result

        if not compatible:
            result = SnapshotSelectionResult(
                selected=False,
                reason=CandidateSelectionReason.INCOMPATIBLE_PROOF.value,
            )
            self._stats.rejected += 1
            return result

        if total_tokens <= 0:
            result = SnapshotSelectionResult(
                selected=False,
                reason=CandidateSelectionReason.EMPTY_PROMPT.value,
            )
            self._stats.rejected += 1
            return result

        if shared < self._min_prefix_tokens:
            result = SnapshotSelectionResult(
                selected=False,
                reason=CandidateSelectionReason.SHARED_PREFIX_BELOW_THRESHOLD.value,
            )
            self._stats.rejected += 1
            return result

        dynamic_tail = max(total_tokens - shared, 0)
        ratio = shared / total_tokens if total_tokens > 0 else 0.0

        candidate = SnapshotCandidate(
            candidate_id=f"cand-{uuid.uuid4().hex[:12]}",
            prefix_hash=getattr(proof, "prefix_hash", "") or "",
            shared_prefix_tokens=shared,
            total_tokens=total_tokens,
            dynamic_tail_tokens=dynamic_tail,
            potential_saved_tokens=shared,
            potential_saved_ratio=ratio,
            model_id=getattr(proof, "model_id", None),
            backend=getattr(proof, "backend", None),
            tokenizer_hash=getattr(proof, "tokenizer_hash", None),
            chat_template_hash=getattr(proof, "chat_template_hash", None),
            compatible=True,
            reason=None,
        )

        score = self.score_candidate(candidate)
        self._stats.selected += 1
        self._stats.potential_saved_tokens_total += shared

        return SnapshotSelectionResult(
            selected=True,
            candidate=candidate,
            score=score,
            reason=CandidateSelectionReason.PROOF_COMPATIBLE.value,
        )

    @staticmethod
    def score_candidate(candidate: SnapshotCandidate) -> CandidateScore:
        """Score a candidate based on potential savings.

        Simple deterministic formula:
          score = ratio * log2(1 + shared_prefix_tokens)

        This rewards high-ratio, high-token-count candidates without
        overfitting to any specific model or workload.
        """
        ratio = candidate.potential_saved_ratio
        shared = candidate.shared_prefix_tokens
        # Bounded log bonus: log2(1 + tokens) caps at ~12 for 4096 tokens
        log_bonus = math.log2(1 + shared)
        raw_score = ratio * log_bonus

        # Normalize to 0–1 range (theoretical max with 32768 tokens: ~15)
        score = min(raw_score / 12.0, 1.0)

        # Confidence
        if shared >= 4096 and ratio >= 0.75:
            confidence = CandidateConfidence.HIGH.value
        elif shared >= 1024 and ratio >= 0.50:
            confidence = CandidateConfidence.MEDIUM.value
        else:
            confidence = CandidateConfidence.LOW.value

        return CandidateScore(
            score=round(score, 4),
            potential_saved_tokens=shared,
            potential_saved_ratio=round(ratio, 4),
            confidence=confidence,
            selection_reason=CandidateSelectionReason.PROOF_COMPATIBLE.value,
        )

    def stats(self) -> dict:
        return self._stats.to_dict()

    def reset(self) -> None:
        self._stats = _SelectionStats()

"""Candidate replay analysis for ThreadWake Phase M11.

Analyzes candidate telemetry from ThreadWakeIndex and optional
SQLite storage to answer: which stable prefixes repeatedly appear,
and which candidates would have been worth reusing?

All output is privacy-safe — no raw prompts, token IDs, opaque refs,
or raw user/thread/request IDs.  SQLite remains optional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────────


@dataclass
class CandidateReplayRecord:
    prefix_hash: str
    backend: str | None = None
    model_id: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    seen_count: int = 0
    potential_saved_tokens_total: int = 0
    average_candidate_score: float = 0.0
    average_potential_saved_ratio: float = 0.0
    confidence: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None

    def safe_dict(self) -> dict:
        return {
            "prefix_hash": self.prefix_hash,
            "backend": self.backend,
            "model_id": self.model_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "seen_count": self.seen_count,
            "potential_saved_tokens_total": self.potential_saved_tokens_total,
            "average_candidate_score": round(self.average_candidate_score, 4),
            "average_potential_saved_ratio": round(self.average_potential_saved_ratio, 4),
            "confidence": self.confidence,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class CandidateReplaySummary:
    total_candidates: int = 0
    total_seen_count: int = 0
    total_potential_saved_tokens: int = 0
    high_confidence_candidates: int = 0
    medium_confidence_candidates: int = 0
    low_confidence_candidates: int = 0
    top_candidates: list[CandidateReplayRecord] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def safe_dict(self) -> dict:
        return {
            "total_candidates": self.total_candidates,
            "total_seen_count": self.total_seen_count,
            "total_potential_saved_tokens": self.total_potential_saved_tokens,
            "high_confidence_candidates": self.high_confidence_candidates,
            "medium_confidence_candidates": self.medium_confidence_candidates,
            "low_confidence_candidates": self.low_confidence_candidates,
            "top_candidates": [c.safe_dict() for c in self.top_candidates],
            "generated_at": self.generated_at,
        }


# ── Analyzer ───────────────────────────────────────────────────────────────


class CandidateReplayAnalyzer:
    """Analyzes candidate telemetry for replay insights.

    Reads from ThreadWakeIndex (in-memory) or SQLite storage (optional).
    Produces a ``CandidateReplaySummary`` with ranked top candidates.
    """

    def analyze_index(self, index: Any, limit: int = 20) -> CandidateReplaySummary:
        """Analyze candidates from a ThreadWakeIndex."""
        candidates = index.list_candidates(limit=500)
        return self._build_summary(candidates, limit)

    def analyze_storage(self, storage: Any, limit: int = 20) -> CandidateReplaySummary:
        """Analyze candidates from a storage adapter."""
        try:
            candidates = storage.list_candidates(limit=500)
        except Exception as exc:
            logger.warning("Replay analysis storage read failed: %s", exc)
            candidates = []
        return self._build_summary(candidates, limit)

    def _build_summary(self, raw: list[dict], limit: int) -> CandidateReplaySummary:
        if not raw:
            return CandidateReplaySummary()

        records: list[dict] = []
        high = medium = low = 0
        total_seen = 0
        total_saved = 0

        for r in raw:
            conf = r.get("candidate_confidence", "low")
            if conf == "high":
                high += 1
            elif conf == "medium":
                medium += 1
            else:
                low += 1
            total_seen += r.get("candidate_seen_count", 0)
            total_saved += r.get("potential_saved_tokens", 0) or 0
            records.append(r)

        top = self.rank_candidates(records, limit)

        return CandidateReplaySummary(
            total_candidates=len(raw),
            total_seen_count=total_seen,
            total_potential_saved_tokens=total_saved,
            high_confidence_candidates=high,
            medium_confidence_candidates=medium,
            low_confidence_candidates=low,
            top_candidates=top,
        )

    @staticmethod
    def rank_candidates(
        records: list[dict],
        limit: int = 20,
    ) -> list[CandidateReplayRecord]:
        """Rank candidates by seen count, saved tokens, score, recency."""
        limit = max(1, min(limit, 100))
        scored: list[tuple[float, dict]] = []
        for r in records:
            seen = r.get("candidate_seen_count", 0) or 0
            saved = r.get("potential_saved_tokens", 0) or 0
            score = r.get("candidate_score", 0) or 0
            # Composite rank: seen_count weighted most, then saved, then score
            rank = (seen * 10000) + (saved * 0.01) + (score * 10)
            scored.append((rank, r))
        scored.sort(key=lambda x: -x[0])

        result: list[CandidateReplayRecord] = []
        for _, r in scored[:limit]:
            result.append(CandidateReplayRecord(
                prefix_hash=r.get("prefix_hash", r.get("prompt_prefix_hash", "")),
                backend=r.get("backend"),
                model_id=r.get("model_id"),
                tokenizer_hash=r.get("tokenizer_hash"),
                chat_template_hash=r.get("chat_template_hash"),
                seen_count=r.get("candidate_seen_count", 0) or 0,
                potential_saved_tokens_total=r.get("potential_saved_tokens", 0) or 0,
                average_candidate_score=r.get("candidate_score", 0) or 0,
                average_potential_saved_ratio=r.get("potential_saved_ratio", 0) or 0,
                confidence=r.get("candidate_confidence"),
                first_seen_at=r.get("first_seen_at"),
                last_seen_at=r.get("last_seen_at") or r.get("candidate_last_seen_at"),
            ))
        return result

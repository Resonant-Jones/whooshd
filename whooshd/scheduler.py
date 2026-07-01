"""Scheduler policy skeleton over the FIFO request queue.

This module provides a scheduling decision layer above the existing
bounded FIFO queue.  Policies:

* ``FIFO`` — oldest queued request runs first (default).
* ``CACHE_AWARE_FIFO`` — prefers ThreadWake cache-ready candidates while
  respecting a fairness bypass limit.

Future schedulers will add batch grouping and continuous batching
policies.  For now, the courthouse has heard its first case.  ⚖️
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

from whooshd.config import get_scheduler_max_bypass, get_scheduler_policy


# ── Enums ──────────────────────────────────────────────────────────────────


class SchedulerPolicy(str, Enum):
    """Available scheduling policies."""

    FIFO = "fifo"
    CACHE_AWARE_FIFO = "cache_aware_fifo"


class SchedulerDecisionReason(str, Enum):
    """Why the scheduler chose (or did not choose) a request."""

    FIFO_OLDEST = "fifo_oldest"
    CACHE_AFFINITY = "cache_affinity"
    FAIRNESS_FIFO = "fairness_fifo"
    NO_ELIGIBLE_REQUEST = "no_eligible_request"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"


# ── Candidate / Decision ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SchedulerCandidate:
    """A queued request candidate for scheduling.

    Contains only safe, metadata-only fields.  No raw prompts, messages,
    generated text, token IDs, KV handles, or opaque refs.
    """

    request_id: str
    queued_at: float
    model: Optional[str] = None
    stream: bool = False
    estimated_tokens: Optional[int] = None
    threadwake_cache_key: Optional[str] = None
    threadwake_stable_prefix_hash: Optional[str] = None
    threadwake_cache_ready: bool = False
    bypass_count: int = 0


@dataclass(frozen=True)
class SchedulerDecision:
    """The result of a scheduling decision.

    When ``request_id`` is None, no request was selected (no capacity,
    no eligible candidates, or no matching policy).
    """

    request_id: Optional[str]
    policy: SchedulerPolicy
    reason: SchedulerDecisionReason
    eligible_count: int


# ── Scheduler ──────────────────────────────────────────────────────────────


class Scheduler:
    """FIFO-first scheduling with experimental cache-aware mode.

    Default policy is ``FIFO``.  When ``CACHE_AWARE_FIFO`` is enabled,
    the scheduler may prefer a newer cache-ready candidate over an older
    non-ready candidate, bounded by a fairness bypass limit.
    """

    def __init__(self) -> None:
        self._last_decision: Optional[SchedulerDecision] = None
        self._bypass_counts: dict[str, int] = {}
        self._cache_affinity_candidates: int = 0
        self._fairness_bypasses: int = 0

    @property
    def policy(self) -> SchedulerPolicy:
        val = get_scheduler_policy()
        if val == "cache_aware_fifo":
            return SchedulerPolicy.CACHE_AWARE_FIFO
        return SchedulerPolicy.FIFO

    @property
    def max_bypass(self) -> int:
        return get_scheduler_max_bypass()

    @property
    def last_decision(self) -> Optional[SchedulerDecision]:
        return self._last_decision

    def choose_next(
        self,
        candidates: Sequence[SchedulerCandidate],
        *,
        capacity_available: bool,
    ) -> SchedulerDecision:
        """Select the next request to dequeue.

        Args:
            candidates: Queued request candidates, ordered by queue position.
            capacity_available: Whether an active execution slot is free.

        Returns:
            A ``SchedulerDecision`` with the selected request ID, or None
            if no request can proceed.
        """
        if not capacity_available:
            decision = SchedulerDecision(
                request_id=None,
                policy=self.policy,
                reason=SchedulerDecisionReason.CAPACITY_UNAVAILABLE,
                eligible_count=len(candidates),
            )
            self._last_decision = decision
            return decision

        if not candidates:
            decision = SchedulerDecision(
                request_id=None,
                policy=self.policy,
                reason=SchedulerDecisionReason.NO_ELIGIBLE_REQUEST,
                eligible_count=0,
            )
            self._last_decision = decision
            return decision

        # Count cache-ready candidates for observability.
        policy = self.policy
        self._cache_affinity_candidates = sum(
            1 for c in candidates if c.threadwake_cache_ready
        )

        if policy == SchedulerPolicy.FIFO:
            return self._choose_fifo(candidates)

        return self._choose_cache_aware(candidates)

    def _choose_fifo(
        self, candidates: Sequence[SchedulerCandidate]
    ) -> SchedulerDecision:
        oldest = min(candidates, key=lambda c: c.queued_at)
        decision = SchedulerDecision(
            request_id=oldest.request_id,
            policy=SchedulerPolicy.FIFO,
            reason=SchedulerDecisionReason.FIFO_OLDEST,
            eligible_count=len(candidates),
        )
        self._last_decision = decision
        return decision

    def _choose_cache_aware(
        self, candidates: Sequence[SchedulerCandidate]
    ) -> SchedulerDecision:
        # Find oldest candidate.
        oldest = min(candidates, key=lambda c: c.queued_at)

        # Fairness: if oldest has been bypassed too many times, it must run.
        bypass_count = self._bypass_counts.get(oldest.request_id, 0)
        if bypass_count >= self.max_bypass:
            decision = SchedulerDecision(
                request_id=oldest.request_id,
                policy=SchedulerPolicy.CACHE_AWARE_FIFO,
                reason=SchedulerDecisionReason.FAIRNESS_FIFO,
                eligible_count=len(candidates),
            )
            self._last_decision = decision
            return decision

        # Find first cache-ready candidate in queue order.
        cache_ready = next(
            (c for c in candidates if c.threadwake_cache_ready),
            None,
        )

        if cache_ready is None or cache_ready.request_id == oldest.request_id:
            # No cache-ready candidate, or oldest is already cache-ready.
            decision = SchedulerDecision(
                request_id=oldest.request_id,
                policy=SchedulerPolicy.CACHE_AWARE_FIFO,
                reason=SchedulerDecisionReason.FIFO_OLDEST,
                eligible_count=len(candidates),
            )
            self._last_decision = decision
            return decision

        # Cache-ready candidate exists and is not the oldest.
        # Increment bypass counts for all older skipped candidates.
        for c in candidates:
            if c.queued_at < cache_ready.queued_at:
                self._bypass_counts[c.request_id] = (
                    self._bypass_counts.get(c.request_id, 0) + 1
                )
                self._fairness_bypasses += 1

        decision = SchedulerDecision(
            request_id=cache_ready.request_id,
            policy=SchedulerPolicy.CACHE_AWARE_FIFO,
            reason=SchedulerDecisionReason.CACHE_AFFINITY,
            eligible_count=len(candidates),
        )
        self._last_decision = decision
        return decision

    def remove_request(self, request_id: str) -> None:
        """Clean up bypass count when a request leaves the queue."""
        self._bypass_counts.pop(request_id, None)

    def build_snapshot(self) -> dict:
        """Return a safe observability snapshot.

        Contains only metadata — no raw prompts, messages, generated text,
        token IDs, or opaque refs.
        """
        last = self._last_decision
        return {
            "policy": self.policy.value,
            "last_decision_reason": last.reason.value if last else None,
            "eligible_count": last.eligible_count if last else 0,
            "cache_affinity_candidates": self._cache_affinity_candidates,
            "fairness_bypasses": self._fairness_bypasses,
            "max_bypass": self.max_bypass,
        }

"""Scheduler policy skeleton over the FIFO request queue.

This module provides a small scheduling decision layer above the existing
bounded FIFO queue.  The default policy is FIFO — oldest queued request
runs first.  No priority lanes, cache affinity, batching, or reordering
are implemented yet.

Future schedulers will add cache-aware selection, batch grouping, and
fairness policies using the same decision interface.  For now, the
courthouse has a bench and a gavel, but no cases on the docket.  ⚖️
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


# ── Enums ──────────────────────────────────────────────────────────────────


class SchedulerPolicy(str, Enum):
    """Available scheduling policies."""

    FIFO = "fifo"


class SchedulerDecisionReason(str, Enum):
    """Why the scheduler chose (or did not choose) a request."""

    FIFO_OLDEST = "fifo_oldest"
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
    """FIFO-only scheduling policy skeleton.

    When capacity is available, the oldest queued request is selected.
    This preserves the existing FIFO behavior while providing a clean
    integration point for future cache-aware, priority, and batching
    policies.
    """

    def __init__(self, policy: SchedulerPolicy = SchedulerPolicy.FIFO) -> None:
        self.policy = policy
        self._last_decision: Optional[SchedulerDecision] = None

    @property
    def last_decision(self) -> Optional[SchedulerDecision]:
        """The most recent scheduling decision, for observability."""
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

        # FIFO: select the oldest queued request.
        # Candidates should already be ordered by queued_at by the queue.
        oldest = min(candidates, key=lambda c: c.queued_at)
        decision = SchedulerDecision(
            request_id=oldest.request_id,
            policy=self.policy,
            reason=SchedulerDecisionReason.FIFO_OLDEST,
            eligible_count=len(candidates),
        )
        self._last_decision = decision
        return decision

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
        }

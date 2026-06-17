"""ThreadWake Phase A eligibility policy + Phase M12 snapshot policy engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .types import PromptGraph, ThreadWakeMode, ThreadWakeObservation, ThreadWakeRequestConfig


DEFAULT_MIN_STABLE_PREFIX_TOKENS = 1024


def evaluate_threadwake_policy(
    graph: PromptGraph | None,
    config: ThreadWakeRequestConfig,
) -> ThreadWakeObservation:
    """Evaluate observe-mode cacheability without touching KV state."""

    mode = config.mode or ThreadWakeMode.OFF
    scope = config.scope or "thread"
    enabled = bool(config.enabled) and mode != ThreadWakeMode.OFF
    min_tokens = (
        config.min_stable_prefix_tokens
        if config.min_stable_prefix_tokens is not None
        else DEFAULT_MIN_STABLE_PREFIX_TOKENS
    )

    if not enabled:
        return ThreadWakeObservation(
            enabled=False,
            mode=ThreadWakeMode.OFF,
            eligible=False,
            reason="threadwake_disabled",
            cache_scope=scope,
        )

    if graph is None:
        return ThreadWakeObservation(
            enabled=True,
            mode=mode,
            eligible=False,
            reason="prompt_graph_missing",
            cache_scope=scope,
        )

    base = {
        "enabled": True,
        "mode": mode,
        "stable_prefix_hash": graph.stable_prefix_hash,
        "stable_prefix_tokens": graph.stable_prefix_tokens,
        "dynamic_tokens": graph.dynamic_tokens,
        "cache_scope": scope,
    }

    if not graph.model_id:
        return ThreadWakeObservation(**base, eligible=False, reason="model_id_missing")
    if not graph.backend:
        return ThreadWakeObservation(**base, eligible=False, reason="backend_missing")
    if mode not in (ThreadWakeMode.OBSERVE, ThreadWakeMode.EPHEMERAL, ThreadWakeMode.SESSION):
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason=f"mode_not_supported: {mode.value}",
        )
    if any(segment.in_stable_prefix and segment.multimodal for segment in graph.segments):
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason="stable_prefix_contains_multimodal",
        )
    if graph.stable_prefix_tokens < min_tokens:
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason="stable_prefix_below_min_tokens",
        )

    return ThreadWakeObservation(
        **base,
        eligible=True,
        reason=None,
        estimated_prefill_reuse_tokens=graph.stable_prefix_tokens,
        cache_hit=False,
    )


# ── Snapshot policy engine (Phase M12) ───────────────────────────────────


class SnapshotEligibilityReason(str, Enum):
    HIGH_FREQUENCY_HIGH_SAVINGS = "high_frequency_high_savings"
    HIGH_FREQUENCY = "high_frequency"
    HIGH_SAVINGS = "high_savings"
    RECENTLY_REUSED = "recently_reused"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    LOW_CONFIDENCE = "low_confidence"
    LOW_VALUE = "low_value"
    EXPIRED = "expired"
    UNSUPPORTED_BACKEND = "unsupported_backend"
    POLICY_DISABLED = "policy_disabled"


@dataclass
class SnapshotEligibility:
    eligible: bool = False
    reason: str = ""
    candidate_score: float = 0.0
    policy_version: str = "1"
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def safe_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "candidate_score": self.candidate_score,
            "policy_version": self.policy_version,
            "evaluated_at": self.evaluated_at,
        }


@dataclass
class SnapshotPolicyConfig:
    enabled: bool = True
    minimum_seen_count: int = 5
    minimum_candidate_score: float = 0.80
    minimum_saved_ratio: float = 0.50
    maximum_candidate_age_days: int = 30
    supported_backends: set[str] = field(default_factory=lambda: {"mlx"})

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "minimum_seen_count": self.minimum_seen_count,
            "minimum_candidate_score": self.minimum_candidate_score,
            "minimum_saved_ratio": self.minimum_saved_ratio,
            "maximum_candidate_age_days": self.maximum_candidate_age_days,
            "supported_backends": sorted(self.supported_backends),
        }


@dataclass
class _PolicyStats:
    evaluations_total: int = 0
    eligible_total: int = 0
    rejected_total: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "evaluations_total": self.evaluations_total,
            "eligible_total": self.eligible_total,
            "rejected_total": self.rejected_total,
            "rejection_reasons": dict(self.rejection_reasons),
        }


class SnapshotPolicyEngine:
    """Deterministic snapshot eligibility evaluator.

    Evaluates candidate replay records against a conservative policy
    to determine whether the candidate would be eligible for future
    snapshot creation.  No snapshots are created.  No KV is mutated.
    """

    POLICY_VERSION = "1"

    def __init__(self, config: SnapshotPolicyConfig | None = None) -> None:
        self.config = config or SnapshotPolicyConfig()
        self._stats = _PolicyStats()

    # ── Public ──────────────────────────────────────────────────────────

    def evaluate_candidate(self, record: Any) -> SnapshotEligibility:
        """Evaluate a single candidate record for snapshot eligibility."""
        self._stats.evaluations_total += 1

        if not self.config.enabled:
            return self._reject(SnapshotEligibilityReason.POLICY_DISABLED.value, 0)

        backend = getattr(record, "backend", None) or ""
        if backend and backend not in self.config.supported_backends:
            return self._reject(SnapshotEligibilityReason.UNSUPPORTED_BACKEND.value, 0)

        seen = getattr(record, "seen_count", 0) or 0
        if seen < self.config.minimum_seen_count:
            return self._reject(
                SnapshotEligibilityReason.INSUFFICIENT_OBSERVATIONS.value,
                getattr(record, "average_candidate_score", 0) or 0,
            )

        score = getattr(record, "average_candidate_score", 0) or 0
        if score < self.config.minimum_candidate_score:
            return self._reject(SnapshotEligibilityReason.LOW_VALUE.value, score)

        ratio = getattr(record, "average_potential_saved_ratio", 0) or 0
        if ratio < self.config.minimum_saved_ratio:
            return self._reject(SnapshotEligibilityReason.LOW_VALUE.value, score)

        # Age check
        last_seen = getattr(record, "last_seen_at", None)
        if last_seen:
            try:
                last_dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - last_dt).days
                if age_days > self.config.maximum_candidate_age_days:
                    return self._reject(SnapshotEligibilityReason.EXPIRED.value, score)
            except (ValueError, TypeError):
                pass

        # Determine eligibility reason
        conf = getattr(record, "confidence", None) or ""
        saved = getattr(record, "potential_saved_tokens_total", 0) or 0
        if seen >= 10 and saved >= 2000:
            reason = SnapshotEligibilityReason.HIGH_FREQUENCY_HIGH_SAVINGS.value
        elif seen >= 10:
            reason = SnapshotEligibilityReason.HIGH_FREQUENCY.value
        elif saved >= 2000:
            reason = SnapshotEligibilityReason.HIGH_SAVINGS.value
        else:
            reason = SnapshotEligibilityReason.RECENTLY_REUSED.value

        self._stats.eligible_total += 1
        return SnapshotEligibility(
            eligible=True, reason=reason, candidate_score=score,
            policy_version=self.POLICY_VERSION,
        )

    def evaluate_replay_summary(self, summary: Any) -> list[SnapshotEligibility]:
        """Evaluate all top candidates from a replay summary."""
        results: list[SnapshotEligibility] = []
        top = getattr(summary, "top_candidates", []) or []
        for record in top:
            results.append(self.evaluate_candidate(record))
        return results

    def policy_stats(self) -> dict:
        return self._stats.to_dict()

    def _reject(self, reason: str, score: float) -> SnapshotEligibility:
        self._stats.rejected_total += 1
        self._stats.rejection_reasons[reason] = self._stats.rejection_reasons.get(reason, 0) + 1
        return SnapshotEligibility(
            eligible=False, reason=reason, candidate_score=score,
            policy_version=self.POLICY_VERSION,
        )

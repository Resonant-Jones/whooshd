"""Batching feasibility analysis — permit office, not conveyor belt.

This module identifies which queued requests could theoretically be
batched together WITHOUT executing any batched inference.  It is
metadata-only, privacy-preserving, and backend-capability aware.

No actual batching occurs.  Default behavior is unchanged.  This is
the permit application before anyone installs motors.  🏗️
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


# ── Enums ──────────────────────────────────────────────────────────────────


class BatchCapability(str, Enum):
    """What level of batching the backend supports."""

    UNSUPPORTED = "unsupported"
    ANALYSIS_ONLY = "analysis_only"


class BatchIncompatibilityReason(str, Enum):
    """Why two requests cannot be batched together."""

    DIFFERENT_MODEL = "different_model"
    DIFFERENT_BACKEND = "different_backend"
    STREAMING_UNSUPPORTED = "streaming_unsupported"
    VISION_UNSUPPORTED = "vision_unsupported"
    SAMPLING_MISMATCH = "sampling_mismatch"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    BACKEND_UNSUPPORTED = "backend_unsupported"


# ── Candidate / Group / Analysis ───────────────────────────────────────────


@dataclass(frozen=True)
class BatchCandidate:
    """A queued request candidate for batching analysis.

    Contains only safe metadata — no raw prompts, messages, generated
    text, token IDs, image content, KV handles, or opaque refs.
    """

    request_id: str
    queued_at: float
    model: str
    backend: Optional[str] = None
    stream: bool = False
    estimated_prompt_tokens: Optional[int] = None
    max_tokens: Optional[int] = None
    sampling_class: Optional[str] = None
    has_image: bool = False
    threadwake_stable_prefix_hash: Optional[str] = None
    threadwake_cache_ready: bool = False


@dataclass(frozen=True)
class BatchGroup:
    """A group of requests that could be batched together.

    When ``eligible`` is False, ``reasons`` explains why.
    """

    group_id: str
    request_ids: tuple[str, ...]
    model: str
    backend: Optional[str] = None
    eligible: bool = False
    reasons: tuple[BatchIncompatibilityReason, ...] = ()
    estimated_total_tokens: Optional[int] = None


@dataclass(frozen=True)
class BatchAnalysis:
    """Result of batching feasibility analysis over queued candidates."""

    enabled: bool
    capability: BatchCapability
    candidate_count: int
    group_count: int
    eligible_group_count: int
    groups: tuple[BatchGroup, ...] = ()
    max_group_size: int = 4
    max_total_tokens: int = 8192


# ── Analyzer ───────────────────────────────────────────────────────────────


class BatchAnalyzer:
    """Group queued candidates into compatible batch groups.

    This is analysis-only.  No actual batching execution occurs.
    The analyzer is backend-capability aware but does not call any
    backend batch APIs.
    """

    def __init__(
        self,
        *,
        max_group_size: int = 4,
        max_total_tokens: int = 8192,
    ) -> None:
        self.max_group_size = max_group_size
        self.max_total_tokens = max_total_tokens

    def analyze(
        self,
        candidates: Sequence[BatchCandidate],
        *,
        enabled: bool = False,
    ) -> BatchAnalysis:
        """Analyze batching feasibility for a set of queued candidates.

        Args:
            candidates: Safe batch candidates from the queue.
            enabled: Whether batch analysis is explicitly enabled.

        Returns:
            A ``BatchAnalysis`` with grouped candidates and eligibility.
        """
        if not enabled or not candidates:
            return BatchAnalysis(
                enabled=enabled,
                capability=BatchCapability.ANALYSIS_ONLY,
                candidate_count=len(candidates),
                group_count=0,
                eligible_group_count=0,
                max_group_size=self.max_group_size,
                max_total_tokens=self.max_total_tokens,
            )

        groups: list[BatchGroup] = []
        remaining = list(candidates)

        while remaining:
            anchor = remaining.pop(0)
            compatible = [anchor]

            # Find compatible candidates from the remaining pool.
            to_remove: list[int] = []
            for i, other in enumerate(remaining):
                if len(compatible) >= self.max_group_size:
                    break
                reasons = _check_compatibility(anchor, other)
                if not reasons:
                    compatible.append(other)
                    to_remove.append(i)

            # Remove matched candidates (reverse order to preserve indices).
            for i in reversed(to_remove):
                remaining.pop(i)

            # Build group.
            request_ids = tuple(c.request_id for c in compatible)
            total_tokens = sum(
                (c.estimated_prompt_tokens or 0) + (c.max_tokens or 0)
                for c in compatible
            ) or None

            eligible = len(compatible) > 1
            group_reasons: tuple[BatchIncompatibilityReason, ...] = ()
            if not eligible:
                group_reasons = (BatchIncompatibilityReason.TOKEN_BUDGET_EXCEEDED,)

            groups.append(BatchGroup(
                group_id=f"batchgrp_{uuid.uuid4().hex[:12]}",
                request_ids=request_ids,
                model=anchor.model,
                backend=anchor.backend,
                eligible=eligible and (total_tokens or 0) <= self.max_total_tokens,
                reasons=group_reasons,
                estimated_total_tokens=total_tokens,
            ))

        eligible_count = sum(1 for g in groups if g.eligible)

        return BatchAnalysis(
            enabled=enabled,
            capability=BatchCapability.ANALYSIS_ONLY,
            candidate_count=len(candidates),
            group_count=len(groups),
            eligible_group_count=eligible_count,
            groups=tuple(groups),
            max_group_size=self.max_group_size,
            max_total_tokens=self.max_total_tokens,
        )

    def build_snapshot(self, analysis: Optional[BatchAnalysis] = None) -> dict:
        """Return a safe observability snapshot."""
        if analysis is None:
            return {
                "enabled": False,
                "capability": BatchCapability.ANALYSIS_ONLY.value,
                "candidate_count": 0,
                "group_count": 0,
                "eligible_group_count": 0,
                "max_group_size": self.max_group_size,
            }
        return {
            "enabled": analysis.enabled,
            "capability": analysis.capability.value,
            "candidate_count": analysis.candidate_count,
            "group_count": analysis.group_count,
            "eligible_group_count": analysis.eligible_group_count,
            "max_group_size": analysis.max_group_size,
        }


# ── Compatibility ──────────────────────────────────────────────────────────


def _check_compatibility(
    a: BatchCandidate,
    b: BatchCandidate,
) -> list[BatchIncompatibilityReason]:
    """Check whether two batch candidates are compatible.

    Returns an empty list if compatible, or a list of incompatibility
    reasons if they cannot be batched together.
    """
    reasons: list[BatchIncompatibilityReason] = []

    if a.model != b.model:
        reasons.append(BatchIncompatibilityReason.DIFFERENT_MODEL)
        return reasons  # Different model = irreconcilable.

    if a.backend != b.backend:
        reasons.append(BatchIncompatibilityReason.DIFFERENT_BACKEND)
        return reasons  # Different backend = irreconcilable.

    if a.stream or b.stream:
        reasons.append(BatchIncompatibilityReason.STREAMING_UNSUPPORTED)

    if a.has_image or b.has_image:
        reasons.append(BatchIncompatibilityReason.VISION_UNSUPPORTED)

    if a.sampling_class != b.sampling_class:
        reasons.append(BatchIncompatibilityReason.SAMPLING_MISMATCH)

    return reasons


# ── Real backend feasibility ──────────────────────────────────────────────


class RealBatchFeasibilityStatus(str, Enum):
    """Probe status for real backend batch execution."""

    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    PROBE_ONLY = "probe_only"
    FEASIBLE = "feasible"
    INCONCLUSIVE = "inconclusive"


class RealBatchBackend(str, Enum):
    """Real backend identifiers for batch feasibility probes."""

    MLX = "mlx"
    LLAMA_CPP = "llama_cpp"


@dataclass(frozen=True)
class RealBatchFeasibilityReport:
    """Metadata-only batch feasibility report for a real backend.

    Contains no raw prompts, rendered prompts, token IDs, generated
    text, cache handles, or model object reprs.
    """

    backend: RealBatchBackend
    status: RealBatchFeasibilityStatus
    explicit_batch_contract: bool = False
    server_side_batching_only: bool = False
    response_order_verified: bool = False
    response_count_verified: bool = False
    prompt_rendering_verified: bool = False
    streaming_supported: bool = False
    vision_supported: bool = False
    prompt_cache_supported: bool = False
    live_path_enabled: bool = False
    notes: tuple[str, ...] = ()


# ── Batch execution ───────────────────────────────────────────────────────


class BatchExecutionCapability(str, Enum):
    """Backend batch execution capability level."""

    UNSUPPORTED = "unsupported"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class BatchExecutionResult:
    """Result of executing a single request within a batch."""

    request_id: str
    response: Any = None
    error: Optional[str] = None
    completed: bool = False

"""Continuous batching implementation planning — beast definition pass.

Separates two tracks: guarded adapter-batch (ship now) vs. true
token-step shared decode scheduler (future work).  No fake mustache
graduation ceremony.  🐉📋
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContinuousBatchingTrack(str, Enum):
    GUARDED_ADAPTER_BATCH = "guarded_adapter_batch"
    TOKEN_STEP_SHARED_DECODE = "token_step_shared_decode"


class ContinuousBatchingTrackStatus(str, Enum):
    RECOMMENDED_NEXT = "recommended_next"
    FUTURE_WORK = "future_work"
    NOT_READY = "not_ready"


@dataclass(frozen=True)
class ContinuousBatchingImplementationDecision:
    recommended_track: ContinuousBatchingTrack = (
        ContinuousBatchingTrack.GUARDED_ADAPTER_BATCH
    )
    future_track: ContinuousBatchingTrack = (
        ContinuousBatchingTrack.TOKEN_STEP_SHARED_DECODE
    )
    production_ready: bool = False
    performance_claim_allowed: bool = False
    default_enablement_allowed: bool = False
    token_step_claim_allowed: bool = False
    required_followups: tuple[str, ...] = (
        "guarded MLX adapter-batch implementation PR",
        "preserve disabled-by-default gates",
        "preserve eligibility envelope",
        "no fallback after generation begins",
        "controlled errors on failure",
        "metadata-only reports",
        "no performance claims without benchmark suite",
    )

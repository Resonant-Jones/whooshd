"""Snapshot manifest system for ThreadWake Phase M13.

Creates sanitized metadata manifests for candidates that pass the
Snapshot Policy Engine.  Manifests represent eligibility for future
snapshot creation — they do NOT contain KV tensors, opaque refs, or
raw prompts/token IDs.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Status / reason ────────────────────────────────────────────────────────


class SnapshotManifestStatus(str, Enum):
    PLANNED = "planned"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    REJECTED = "rejected"


class SnapshotManifestReason(str, Enum):
    POLICY_ELIGIBLE = "policy_eligible"
    SUPERSEDED_BY_NEWER = "superseded_by_newer_manifest"
    EXPIRED_BY_POLICY = "expired_by_policy"
    INVALIDATED_BY_MODEL_CHANGE = "invalidated_by_model_change"
    INVALIDATED_BY_TOKENIZER_CHANGE = "invalidated_by_tokenizer_change"
    INVALIDATED_BY_TEMPLATE_CHANGE = "invalidated_by_template_change"


# ── Manifest ───────────────────────────────────────────────────────────────


@dataclass
class SnapshotManifest:
    manifest_id: str
    prefix_hash: str
    backend: str | None = None
    model_id: str | None = None
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    candidate_score: float = 0.0
    candidate_confidence: str | None = None
    seen_count: int = 0
    potential_saved_tokens_total: int = 0
    average_potential_saved_ratio: float = 0.0
    eligibility_reason: str = ""
    policy_version: str = "1"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at: str | None = None
    status: str = SnapshotManifestStatus.PLANNED.value

    def safe_dict(self) -> dict:
        return {
            "manifest_id": self.manifest_id,
            "prefix_hash": self.prefix_hash,
            "backend": self.backend,
            "model_id": self.model_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "candidate_score": self.candidate_score,
            "candidate_confidence": self.candidate_confidence,
            "seen_count": self.seen_count,
            "potential_saved_tokens_total": self.potential_saved_tokens_total,
            "average_potential_saved_ratio": round(self.average_potential_saved_ratio, 4),
            "eligibility_reason": self.eligibility_reason,
            "policy_version": self.policy_version,
            "status": self.status,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


# ── Builder ────────────────────────────────────────────────────────────────


class SnapshotManifestBuilder:
    """Builds sanitized snapshot manifests from replay records and eligibility."""

    @staticmethod
    def build_from_replay_record(
        record: Any,
        eligibility: Any,
    ) -> SnapshotManifest | None:
        """Build a manifest from a CandidateReplayRecord and SnapshotEligibility.

        Returns None if eligibility.eligible is False.
        """
        if not getattr(eligibility, "eligible", False):
            return None

        prefix = getattr(record, "prefix_hash", "") or ""
        backend = getattr(record, "backend", None)
        model = getattr(record, "model_id", None)
        tok_hash = getattr(record, "tokenizer_hash", None)
        tmpl_hash = getattr(record, "chat_template_hash", None)
        policy_ver = getattr(eligibility, "policy_version", "1") or "1"

        manifest_id = SnapshotManifestBuilder.manifest_id_for(
            prefix, backend or "", model or "", tok_hash or "", tmpl_hash or "", policy_ver,
        )

        return SnapshotManifest(
            manifest_id=manifest_id,
            prefix_hash=prefix,
            backend=backend,
            model_id=model,
            tokenizer_hash=tok_hash,
            chat_template_hash=tmpl_hash,
            candidate_score=getattr(record, "average_candidate_score", 0) or 0,
            candidate_confidence=getattr(record, "confidence", None),
            seen_count=getattr(record, "seen_count", 0) or 0,
            potential_saved_tokens_total=getattr(record, "potential_saved_tokens_total", 0) or 0,
            average_potential_saved_ratio=getattr(record, "average_potential_saved_ratio", 0) or 0,
            eligibility_reason=getattr(eligibility, "reason", ""),
            policy_version=policy_ver,
            last_seen_at=getattr(record, "last_seen_at", None),
        )

    @staticmethod
    def manifest_id_for(
        prefix_hash: str,
        backend: str,
        model_id: str,
        tokenizer_hash: str,
        chat_template_hash: str,
        policy_version: str,
    ) -> str:
        """Deterministic manifest ID from prefix + metadata hashes."""
        payload = f"{prefix_hash}|{backend}|{model_id}|{tokenizer_hash}|{chat_template_hash}|{policy_version}"
        return f"manifest-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"

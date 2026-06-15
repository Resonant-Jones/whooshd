"""ThreadWake metrics with bounded-cardinality label dimensions.

Counters track hits, misses, evictions, matched tokens, and ineligible
requests.  Label values are drawn from bounded enums to prevent
high-cardinality explosion.  No raw hashes or user identifiers are
ever used as label values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .types import ThreadWakeObservation

# ── Bounded label enums ────────────────────────────────────────────────────

MetricMode = Literal["observe", "ephemeral", "session", "advanced", "off"]
MetricReason = Literal[
    "eligible",
    "threadwake_disabled",
    "prompt_graph_missing",
    "model_id_missing",
    "backend_missing",
    "mode_not_supported",
    "stable_prefix_contains_multimodal",
    "stable_prefix_below_min_tokens",
    "scope_not_compatible",
    "backend_unsupported",
    "backend_unknown",
    "backend_failure",
    "other",
]
MetricScope = Literal["request", "thread", "project", "user", "global"]
MetricBackend = str  # Bounded by registered backends


@dataclass
class ThreadWakeMetrics:
    """In-memory counters with label dimensions.

    All label values are drawn from bounded enums or short
    known-constant strings (backend names).  No raw hashes,
    user IDs, or KV refs appear in label values.
    """

    # Flat counters (no labels — safe for any metric backend)
    counters: dict[str, int] = field(default_factory=lambda: {
        "threadwake_observations_total": 0,
        "threadwake_eligible_total": 0,
        "threadwake_ineligible_total": 0,
        "threadwake_estimated_reuse_tokens_total": 0,
        "threadwake_cache_hits_total": 0,
        "threadwake_cache_misses_total": 0,
        "threadwake_cache_evictions_total": 0,
        "threadwake_prefix_tokens_matched_total": 0,
    })

    # Labeled counters: (metric_name, label_dict) → count
    labeled: dict[tuple, int] = field(default_factory=dict)

    def record(self, observation: ThreadWakeObservation) -> None:
        self.counters["threadwake_observations_total"] += 1

        if observation.eligible:
            self.counters["threadwake_eligible_total"] += 1
            self.counters["threadwake_estimated_reuse_tokens_total"] += (
                observation.estimated_prefill_reuse_tokens
            )
            if observation.cache_hit:
                self.counters["threadwake_cache_hits_total"] += 1
                self.counters["threadwake_prefix_tokens_matched_total"] += (
                    observation.estimated_prefill_reuse_tokens
                )
            else:
                self.counters["threadwake_cache_misses_total"] += 1

            self._inc_labeled("hits", observation, "eligible")
        else:
            self.counters["threadwake_ineligible_total"] += 1
            reason = _coerce_reason(observation.reason)
            self._inc_labeled("misses", observation, reason)

    def record_eviction(self, count: int = 1) -> None:
        self.counters["threadwake_cache_evictions_total"] += count

    def _inc_labeled(
        self,
        outcome: str,
        observation: ThreadWakeObservation,
        reason: MetricReason,
    ) -> None:
        mode: MetricMode = observation.mode.value  # type: ignore[assignment]
        scope: MetricScope = observation.cache_scope  # type: ignore[assignment]
        backend = observation.backend_kv_capability or "unknown"
        key = (f"threadwake_{outcome}_total", mode, scope, backend, reason)
        self.labeled[key] = self.labeled.get(key, 0) + 1

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)

    def labeled_snapshot(self) -> dict[str, int]:
        """Return labeled counters keyed by readable metric name + labels."""
        result: dict[str, int] = {}
        for (metric, mode, scope, backend, reason), count in self.labeled.items():
            key = f"{metric}{{mode=\"{mode}\",scope=\"{scope}\",backend=\"{backend}\",reason=\"{reason}\"}}"
            result[key] = count
        return result

    def reset(self) -> None:
        for key in self.counters:
            self.counters[key] = 0
        self.labeled.clear()


_DEFAULT_METRICS = ThreadWakeMetrics()


def get_threadwake_metrics() -> ThreadWakeMetrics:
    return _DEFAULT_METRICS


# ── Helpers ────────────────────────────────────────────────────────────────


_REASON_MAP: dict[str, MetricReason] = {
    "threadwake_disabled": "threadwake_disabled",
    "prompt_graph_missing": "prompt_graph_missing",
    "model_id_missing": "model_id_missing",
    "backend_missing": "backend_missing",
    "backend_capable_but_ineligible": "backend_missing",
    "mode_not_supported": "mode_not_supported",
    "stable_prefix_contains_multimodal": "stable_prefix_contains_multimodal",
    "stable_prefix_below_min_tokens": "stable_prefix_below_min_tokens",
    "backend_unsupported": "backend_unsupported",
    "backend_unknown": "backend_unknown",
    "observe_mode_not_reusing": "mode_not_supported",
    "scope_not_compatible": "scope_not_compatible",
    "backend_failure": "backend_failure",
}


def _coerce_reason(raw_reason: str | None) -> MetricReason:
    if raw_reason is None:
        return "eligible"
    # Try exact match first
    if raw_reason in _REASON_MAP:
        return _REASON_MAP[raw_reason]
    # Check prefix matches (e.g., "mode_not_supported: ephemeral" → mode_not_supported)
    for key in _REASON_MAP:
        if raw_reason.startswith(key):
            return _REASON_MAP[key]
    return "other"

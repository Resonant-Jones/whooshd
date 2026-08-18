"""Capacity profile — structured evidence for model/runtime concurrency ceilings.

A :class:`CapacityProfile` is the durable artifact produced by the
``capacity-bench`` CLI.  It captures *measured* evidence per active
concurrency band for a single ``(model, runtime, machine)`` triple.

The profile is intentionally metadata-only:

* No raw prompts, rendered prompts, or generated text.
* No token IDs, KV cache handles, or opaque refs.
* No user-identifying data.

It is safe to serialise, persist alongside the model registry, and to
load into the capacity controller at runtime.

This module is the *schema*.  The :mod:`whooshd.capacity_controller`
module is the *consumer*.  Benchmarks emit profiles; controllers read
profiles.  Keeping them separate means new benchmark modes (e.g.
re-calibration, automated nightly runs) do not require controller
changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────


class CapacityMode(str, Enum):
    """How the capacity controller decides how many requests may run."""

    FIXED = "fixed"          # WHOOSHD_MAX_ACTIVE_REQUESTS is the hard ceiling.
    ADAPTIVE = "adaptive"    # Profile-calibrated limit, capped by operator ceiling.


class MemoryPressureClass(str, Enum):
    """Qualitative memory pressure classification."""

    UNKNOWN = "unknown"
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


class CapacityDecisionReason(str, Enum):
    """Structured reason for a capacity controller decision.

    Surfaced through the snapshot so dashboards can explain *why* a
    request was admitted, queued, or rejected.
    """

    BELOW_OPERATOR_CEILING = "below_operator_ceiling"
    CALIBRATED_LIMIT = "calibrated_limit"
    OPERATOR_CEILING = "operator_ceiling"
    MISSING_PROFILE = "missing_profile"
    INVALID_PROFILE = "invalid_profile"
    MEMORY_PRESSURE_HIGH = "memory_pressure_high"
    QUEUE_FULL = "queue_full"
    RUNTIME_NOT_READY = "runtime_not_ready"
    BATCH_ACTIVE_LIMIT_REACHED = "batch_active_limit_reached"


# ── Per-band evidence ──────────────────────────────────────────────────────


class CapacityBand(BaseModel):
    """Evidence captured for a single active concurrency band.

    All numeric fields are measured (not estimated).  Missing data is
    represented as ``None`` — never zero, which is a meaningful value.
    """

    concurrency: int = Field(..., ge=1, description="Active concurrency tested")
    successful: bool = Field(False, description="Whether the band ran without OOM/fatal errors")
    request_count: int = Field(0, ge=0, description="Number of requests executed in this band")

    # ── Latency ─────────────────────────────────────────────────────
    ttft_p50_ms: Optional[float] = Field(None, ge=0, description="Median time-to-first-token (ms)")
    ttft_p95_ms: Optional[float] = Field(None, ge=0, description="95th percentile TTFT (ms)")
    latency_p50_ms: Optional[float] = Field(None, ge=0, description="Median total latency (ms)")
    latency_p95_ms: Optional[float] = Field(None, ge=0, description="95th percentile total latency (ms)")

    # ── Throughput ──────────────────────────────────────────────────
    aggregate_tokens_per_second: Optional[float] = Field(
        None, ge=0, description="Total generated tokens / wall-clock seconds for the band"
    )
    per_request_tokens_per_second: Optional[float] = Field(
        None, ge=0, description="Mean generated tokens/sec per request"
    )

    # ── Counts ──────────────────────────────────────────────────────
    success_count: int = Field(0, ge=0)
    overload_count: int = Field(0, ge=0, description="429 / overload responses")
    failure_count: int = Field(0, ge=0, description="5xx / runtime errors")
    stuck_count: int = Field(0, ge=0, description="Requests that did not complete within timeout")

    # ── Memory ──────────────────────────────────────────────────────
    memory_pressure: MemoryPressureClass = MemoryPressureClass.UNKNOWN
    runtime_memory_mb: Optional[float] = Field(None, ge=0)
    host_memory_pressure: Optional[MemoryPressureClass] = Field(None)

    # ── Verdict ─────────────────────────────────────────────────────
    notes: tuple[str, ...] = Field(default_factory=tuple)


# ── Profile artifact ───────────────────────────────────────────────────────


class CapacityProfile(BaseModel):
    """Structured capacity evidence for a model/runtime/machine triple.

    The profile is the artifact emitted by ``capacity-bench`` and
    consumed by the capacity controller.  It does not store prompt
    content, generated text, or user identifiers.
    """

    # ── Identity ────────────────────────────────────────────────────
    model_id: str = Field(..., description="Model identifier (HF repo or local path)")
    runtime: str = Field(..., description="Runtime adapter name (mlx, mlx_lm_server, llama_cpp, stub)")
    machine_class: str = Field(..., description="Stable machine identifier, e.g. apple-silicon-m4-32gb")
    host_memory_bytes: Optional[int] = Field(None, ge=0)

    # ── Benchmark config ────────────────────────────────────────────
    prompt_size_chars: int = Field(..., ge=0, description="Approximate prompt size in characters")
    configured_max_tokens: int = Field(..., ge=0)
    streaming: bool = Field(False)

    # ── Evidence ────────────────────────────────────────────────────
    bands: tuple[CapacityBand, ...] = Field(default_factory=tuple)
    recommended_active_concurrency: int = Field(
        1, ge=1, description="Operator-recommended concurrency after evidence review"
    )

    # ── Provenance ──────────────────────────────────────────────────
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    benchmark_version: str = Field("1.0", description="capacity-bench protocol version")


# ── In-memory profile loader ───────────────────────────────────────────────


def safe_load_profile(path: str) -> Optional[CapacityProfile]:
    """Load a :class:`CapacityProfile` from JSON.

    Returns ``None`` if the file is missing, unreadable, or fails
    schema validation.  Never raises — controllers must be able to
    start up with no profile present.
    """
    import json
    import os

    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CapacityProfile.model_validate(data)
    except Exception:
        return None


def safe_save_profile(profile: CapacityProfile, path: str) -> bool:
    """Persist a :class:`CapacityProfile` to JSON.  Returns success."""
    import json
    import os

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(mode="json"), f, indent=2)
        return True
    except Exception:
        return False
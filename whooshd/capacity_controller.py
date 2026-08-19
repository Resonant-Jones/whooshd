"""Capacity controller — answers "how many requests may execute right now?".

The capacity controller is the single source of truth for *active*
concurrency decisions.  It does **not** own inference, scheduling
policy, queue selection, or backend execution.

Inputs:
    * Configured operator ceiling (``WHOOSHD_MAX_ACTIVE_REQUESTS``).
    * Mode (``fixed`` or ``adaptive``).
    * Optional measured :class:`CapacityProfile`.
    * Current active / queued counts.
    * Current memory pressure.
    * Runtime adapter availability.

Outputs:
    * :meth:`effective_active_limit` — int, ``>= 1``.
    * :meth:`evaluate` — a :class:`CapacityEvaluation` carrying
      ``decision`` ∈ {RUN, QUEUE, REJECT} plus a structured
      :class:`CapacityDecisionReason`.

Modes:
    * ``fixed`` — deterministic.  Effective limit equals the operator
      ceiling.  This is the default and matches pre-throughput
      behaviour exactly.
    * ``adaptive`` — measures the operator ceiling, the calibrated
      profile, and current memory pressure.  Effective limit is
      ``min(operator_ceiling, calibrated_recommended)`` under normal
      memory pressure, and may drop further under high pressure.

The controller is intentionally a thin object.  It holds **no I/O**,
no asyncio, no state of its own beyond a cached profile loader.  This
keeps it deterministic and trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from whooshd.capacity_profile import (
    CapacityBand,
    CapacityDecisionReason,
    CapacityMode,
    CapacityProfile,
    MemoryPressureClass,
    safe_load_profile,
)
from whooshd.config import (
    get_capacity_machine_class,
    get_capacity_memory_pressure_deny,
    get_capacity_model_id,
    get_capacity_mode,
    get_capacity_profile_path,
    get_capacity_runtime,
    get_max_active_requests,
)


class CapacityAction(str, Enum):
    """Verdict emitted by :meth:`CapacityController.evaluate`."""

    RUN = "run"          # An active slot is free; caller may execute.
    QUEUE = "queue"      # No slot free / denied; caller should queue.
    REJECT = "reject"    # Cannot ever queue (queue full); caller must reject.


@dataclass(frozen=True)
class CapacityEvaluation:
    """Result of a capacity controller decision."""

    action: CapacityAction
    effective_active_limit: int
    operator_ceiling: int
    mode: CapacityMode
    reason: CapacityDecisionReason
    calibrated_concurrency: Optional[int] = None
    memory_pressure: MemoryPressureClass = MemoryPressureClass.UNKNOWN
    profile_loaded: bool = False
    profile_eligible: bool = False
    profile_rejection_reason: Optional[str] = None

    def snapshot(self) -> dict:
        """Safe observability snapshot.  No prompt or runtime content."""
        return {
            "action": self.action.value,
            "effective_active_limit": self.effective_active_limit,
            "operator_ceiling": self.operator_ceiling,
            "mode": self.mode.value,
            "reason": self.reason.value,
            "calibrated_concurrency": self.calibrated_concurrency,
            "memory_pressure": self.memory_pressure.value,
            "profile_loaded": self.profile_loaded,
            "profile_eligible": self.profile_eligible,
            "profile_rejection_reason": self.profile_rejection_reason,
        }


@dataclass(frozen=True)
class CapacityContext:
    """Inputs the controller considers when deciding.

    All fields are required to make a decision; pass ``memory_pressure``
    as :attr:`MemoryPressureClass.UNKNOWN` when no host signal is
    available.
    """

    active_jobs: int
    queue_depth: int
    max_queue_depth: int
    runtime_ready: bool
    memory_pressure: MemoryPressureClass = MemoryPressureClass.UNKNOWN
    requested_estimated_tokens: Optional[int] = None  # forward-looking cost hint


@dataclass(frozen=True)
class CapacityBandVerdict:
    """The verdict the controller made about a single benchmark band.

    Used by the capacity controller itself to *choose* a calibrated
    concurrency from a :class:`CapacityProfile`.  Kept here (not in the
    profile module) because it is a controller-internal concept.
    """

    band: CapacityBand
    score: float  # higher = better candidate
    reasons: tuple[str, ...] = ()


# ── Controller ─────────────────────────────────────────────────────────────


class CapacityController:
    """Decides how many requests may execute *now*.

    Stateless beyond a cached profile loader.  Construct one per
    process, share freely — :meth:`evaluate` is pure with respect to
    its inputs (it does, however, read the configured operator ceiling
    at call time so env changes are picked up).
    """

    def __init__(self, *, profile_path: Optional[str] = None) -> None:
        self._profile_path = profile_path
        self._cached_profile: Optional[CapacityProfile] = None
        self._cached_profile_signature: Optional[tuple[str, Optional[float]]] = None

    # ── Profile loader ───────────────────────────────────────────────

    def _get_profile(self) -> Optional[CapacityProfile]:
        """Load the profile if configured.  Cached by path+mtime."""
        import os

        path = self._profile_path or get_capacity_profile_path()
        if not path:
            return None
        try:
            mtime: Optional[float] = os.path.getmtime(path)
        except OSError:
            mtime = None
        sig = (path, mtime)
        if self._cached_profile_signature == sig and self._cached_profile is not None:
            return self._cached_profile
        profile = safe_load_profile(path)
        self._cached_profile_signature = sig
        self._cached_profile = profile
        return profile

    def invalidate_profile_cache(self) -> None:
        """Force the next call to reload from disk."""
        self._cached_profile_signature = None
        self._cached_profile = None

    @staticmethod
    def _host_memory_bytes() -> Optional[int]:
        """Best-effort physical-memory identity for profile isolation."""
        import os

        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            if page_size and pages:
                return int(page_size) * int(pages)
        except (OSError, ValueError):
            pass
        return None

    def _profile_status(self) -> tuple[Optional[CapacityProfile], bool, Optional[str]]:
        """Return the loaded profile and its exact-target eligibility."""
        profile = self._get_profile()
        if profile is None:
            return None, False, "missing_profile"

        model_id = get_capacity_model_id()
        runtime = get_capacity_runtime()
        machine_class = get_capacity_machine_class()
        if not model_id or not runtime or not machine_class:
            return profile, False, "identity_unavailable"
        if profile.model_id != model_id:
            return profile, False, "model_mismatch"
        if profile.runtime != runtime:
            return profile, False, "runtime_mismatch"
        if profile.machine_class != machine_class:
            return profile, False, "machine_mismatch"

        current_memory = self._host_memory_bytes()
        if (
            profile.host_memory_bytes is not None
            and current_memory is not None
            and profile.host_memory_bytes != current_memory
        ):
            return profile, False, "host_memory_mismatch"
        if profile.host_memory_bytes is not None and current_memory is None:
            return profile, False, "host_memory_unavailable"
        return profile, True, None

    # ── Mode / ceiling ──────────────────────────────────────────────

    @property
    def mode(self) -> CapacityMode:
        val = get_capacity_mode()
        return CapacityMode.ADAPTIVE if val == "adaptive" else CapacityMode.FIXED

    @property
    def operator_ceiling(self) -> int:
        return max(get_max_active_requests(), 1)

    def effective_active_limit(
        self,
        *,
        memory_pressure: MemoryPressureClass = MemoryPressureClass.UNKNOWN,
    ) -> int:
        """Return the active concurrency limit, taking mode and pressure
        into account.

        Always ``>= 1``.  Adaptive mode never exceeds the operator
        ceiling.
        """
        ceiling = self.operator_ceiling

        if self.mode == CapacityMode.FIXED:
            return ceiling

        profile, eligible, _ = self._profile_status()
        if profile is None or not eligible:
            return ceiling

        calibrated = profile.recommended_active_concurrency
        # Conservative: never raise above the operator ceiling.
        limit = min(ceiling, calibrated)

        # Memory pressure may further reduce the limit.  We don't
        # *raise* the limit under low memory pressure — only the
        # operator can do that, by editing the profile.
        if (
            get_capacity_memory_pressure_deny()
            and memory_pressure in (MemoryPressureClass.HIGH, MemoryPressureClass.CRITICAL)
            and limit > 1
        ):
            limit = max(limit // 2, 1)

        return max(limit, 1)

    # ── Decision API ────────────────────────────────────────────────

    def evaluate(self, ctx: CapacityContext) -> CapacityEvaluation:
        """Decide what to do with an incoming request.

        Returns a :class:`CapacityEvaluation`.  The decision is one of
        :class:`CapacityAction`.  The reason is a structured enum
        suitable for telemetry.
        """
        operator_ceiling = self.operator_ceiling
        mode = self.mode
        profile: Optional[CapacityProfile] = None
        profile_loaded = False
        profile_eligible = False
        profile_rejection_reason: Optional[str] = None
        calibrated: Optional[int] = None
        if mode == CapacityMode.ADAPTIVE:
            profile, profile_eligible, profile_rejection_reason = self._profile_status()
            profile_loaded = profile is not None
            if profile_eligible and profile is not None:
                calibrated = profile.recommended_active_concurrency
        profile_fields = {
            "calibrated_concurrency": calibrated,
            "profile_loaded": profile_loaded,
            "profile_eligible": profile_eligible,
            "profile_rejection_reason": profile_rejection_reason,
        }

        if not ctx.runtime_ready:
            return CapacityEvaluation(
                action=CapacityAction.REJECT,
                effective_active_limit=self.effective_active_limit(
                    memory_pressure=ctx.memory_pressure,
                ),
                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=CapacityDecisionReason.RUNTIME_NOT_READY,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
            )

        if ctx.queue_depth >= ctx.max_queue_depth:
            return CapacityEvaluation(
                action=CapacityAction.REJECT,
                effective_active_limit=self.effective_active_limit(
                    memory_pressure=ctx.memory_pressure,
                ),
                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=CapacityDecisionReason.QUEUE_FULL,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
            )

        limit = self.effective_active_limit(memory_pressure=ctx.memory_pressure)

        # Decide: can we run?
        if ctx.active_jobs < limit:
            # Memory-pressure override: even with a free slot, queue
            # under high pressure if deny is enabled.  This is a
            # conservative seam; future cost-aware controllers will
            # also feed ``requested_estimated_tokens`` here.
            if (
                get_capacity_memory_pressure_deny()
                and ctx.memory_pressure == MemoryPressureClass.HIGH
                and limit <= ctx.active_jobs + 1
            ):
                return CapacityEvaluation(
                    action=CapacityAction.QUEUE,
                    effective_active_limit=limit,
                    operator_ceiling=operator_ceiling,
                    mode=mode,
                    reason=CapacityDecisionReason.MEMORY_PRESSURE_HIGH,
                    memory_pressure=ctx.memory_pressure,
                    **profile_fields,
                )

            if mode == CapacityMode.ADAPTIVE and calibrated is not None:
                reason = (
                    CapacityDecisionReason.CALIBRATED_LIMIT
                    if calibrated < operator_ceiling
                    else CapacityDecisionReason.OPERATOR_CEILING
                )
            else:
                reason = CapacityDecisionReason.BELOW_OPERATOR_CEILING
            return CapacityEvaluation(
                action=CapacityAction.RUN,
                effective_active_limit=limit,
                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=reason,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
            )

        # No slot free — admit to queue.
        return CapacityEvaluation(
            action=CapacityAction.QUEUE,
            effective_active_limit=limit,
            operator_ceiling=operator_ceiling,
            mode=mode,
            reason=CapacityDecisionReason.CALIBRATED_LIMIT
            if mode == CapacityMode.ADAPTIVE and calibrated is not None
            else CapacityDecisionReason.OPERATOR_CEILING,
            memory_pressure=ctx.memory_pressure,
            **profile_fields,
        )

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(
        self,
        *,
        active_jobs: int,
        queue_depth: int,
        max_queue_depth: int,
        runtime_ready: bool,
        memory_pressure: MemoryPressureClass = MemoryPressureClass.UNKNOWN,
    ) -> dict:
        """Safe observability snapshot — no prompt/runtime content."""
        eval_ = self.evaluate(
            CapacityContext(
                active_jobs=active_jobs,
                queue_depth=queue_depth,
                max_queue_depth=max_queue_depth,
                runtime_ready=runtime_ready,
                memory_pressure=memory_pressure,
            )
        )
        snap = eval_.snapshot()
        snap["capacity_mode"] = snap.get("mode", eval_.mode.value)
        snap["capacity_reason"] = snap.get("reason", eval_.reason.value)
        snap["active_jobs"] = active_jobs
        snap["queue_depth"] = queue_depth
        snap["max_queue_depth"] = max_queue_depth
        snap["runtime_ready"] = runtime_ready
        profile, _, _ = self._profile_status() if self.mode == CapacityMode.ADAPTIVE else (None, False, None)
        snap["profile_model_id"] = profile.model_id if profile is not None else None
        snap["profile_runtime"] = profile.runtime if profile is not None else None
        snap["profile_machine_class"] = profile.machine_class if profile is not None else None
        snap["configured_capacity_model_id"] = get_capacity_model_id()
        snap["configured_capacity_runtime"] = get_capacity_runtime()
        snap["configured_capacity_machine_class"] = get_capacity_machine_class()
        return snap

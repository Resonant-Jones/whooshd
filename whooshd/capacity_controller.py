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

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from whooshd.capacity_context import RequestCost

from whooshd.capacity_profile import (
    ContextBudgets,
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


# A local snapshot path names one *revision* of a repository.  The
# revision is a version marker, not part of the repository's identity,
# so it is stripped before the repository name is parsed.  Without this,
# ``org/name/snapshots/<revision>`` would be read as a repository whose
# name is ``name/snapshots/<revision>``, which matches nothing.
_SNAPSHOT_REVISION_RE = re.compile(r"/snapshots/[^/]+$")


def _canonical_model_identity(value: str) -> tuple[Optional[str], str]:
    """Reduce a model identity to ``(repository_identity, bare_name)``.

    ``repository_identity`` is the namespaced form (``org/name``) when the
    identity carries one, and ``None`` when it does not.  ``bare_name`` is
    the final segment, always present.

    The namespaced form is what prevents two repositories that share a
    basename from being conflated::

        org-a/foo  ->  ("org-a/foo", "foo")
        org-b/foo  ->  ("org-b/foo", "foo")

    Known local snapshot layouts are canonicalised rather than truncated:

    * the Hugging Face cache directory ``models--org--name`` becomes
      ``org/name`` — the same weights under the repository's real name;
    * a snapshot path is reduced to the repository it points at, because
      the revision after ``snapshots/`` is not part of the identity;
    * a filesystem path keeps only its final segment, because a parent
      directory is a *location*, not a namespace, and must not be
      mistaken for one.
    """
    text = (value or "").strip().rstrip("/")
    if not text:
        return (None, "")

    # A snapshot revision is not part of the repository's identity.
    text = _SNAPSHOT_REVISION_RE.sub("", text)

    # Hugging Face cache layout: models--<org>--<name>
    if "models--" in text:
        tail = text.rsplit("models--", 1)[1]
        # The cache directory is the boundary.  Everything after it
        # (``snapshots/<revision>``, ``blobs/<sha>``, ``refs/<branch>``)
        # is cache layout, not identity, so the name is read from the
        # directory itself and never from the path below it.
        head = tail.split("/", 1)[0]
        org, sep, name = head.partition("--")
        if sep and org and name:
            return (f"{org.lower()}/{name.lower()}", name.lower())
        return (None, head.lower())

    # A filesystem path: the parent directory is a location, not a
    # namespace.
    if text.startswith(("/", "./", "../", "~")):
        return (None, text.rsplit("/", 1)[-1].lower())

    # A repository id: org/name
    if "/" in text:
        return (text.lower(), text.rsplit("/", 1)[-1].lower())

    # A bare name.
    return (None, text.lower())


def model_identity_matches(profile_model_id: str, candidate: str) -> bool:
    """Whether *candidate* names the same model as *profile_model_id*.

    A match is asserted only when the two identities can be shown to be the
    same:

    * **both** carry a repository identity — the namespaced forms must be
      equal.  ``org-a/foo`` and ``org-b/foo`` are therefore *not* the same
      model, however similar their final segments;
    * **neither** carries a repository identity — the bare names must be
      equal, because there is no namespace to compare;
    * **exactly one** carries a repository identity — the identities cannot
      be confirmed equal, so they are not treated as a match.  An
      unconfirmed match would apply one model's ceiling to another, which
      is the failure this function exists to prevent.

    A local snapshot path and the corresponding repository id for the same
    weights *are* recognised as the same model, because the snapshot layout
    carries the repository's real name.
    """
    left_repo, left_name = _canonical_model_identity(profile_model_id)
    right_repo, right_name = _canonical_model_identity(candidate)

    if not left_name or not right_name:
        return False
    if left_repo and right_repo:
        return left_repo == right_repo
    if left_repo or right_repo:
        return False
    return left_name == right_name


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

    # -- Per-model context policy ---------------------------------------
    # Bounded, metadata-only evidence of how the request compared to the
    # model's context budgets.  ``None`` means no budget was applied.
    context_reason: Optional[str] = None
    context_budget: Optional[str] = None
    context_total_tokens: Optional[int] = None
    context_breached_budget: Optional[str] = None

    # -- Route-specific capacity -------------------------------------
    # The lane that will execute this request, that lane's own ceiling,
    # and whether the lane (rather than the global limit) is what bound
    # the decision.
    lane: Optional[str] = None
    lane_max_concurrent: Optional[int] = None
    lane_active_jobs: Optional[int] = None
    lane_limited: bool = False

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
            # Context policy labels only.  The request-derived token counts
            # stay on the evaluation object and are surfaced through the
            # per-request admission details (which already carry
            # ``estimated_chars``), not through this state snapshot.
            "context_reason": self.context_reason,
            "context_budget": self.context_budget,
            "context_breached_budget": self.context_breached_budget,
            "lane": self.lane,
            "lane_max_concurrent": self.lane_max_concurrent,
            "lane_active_jobs": self.lane_active_jobs,
            "lane_limited": self.lane_limited,
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
    #: The request's full cost breakdown, when admission has computed one.
    #: The controller evaluates the *real* cost against the budgets so that
    #: per-direction maxima (declared input/output limits) and the exactness
    #: of the estimate are honoured.  When absent, only
    #: ``requested_estimated_tokens`` is available and the comparison is
    #: made against the combined total alone.
    request_cost: Optional["RequestCost"] = None
    #: Per-model context budgets to compare the request cost against.  When
    #: absent the request is admitted unchanged -- an unmeasured model is
    #: never silently constrained.
    context_budgets: Optional["ContextBudgets"] = None
    #: Inference lane that will execute this request, and that lane's own
    #: concurrency ceiling (None when the lane declares none).
    lane: Optional[str] = None
    lane_max_concurrent: Optional[int] = None
    #: Active requests already bound for this lane.
    lane_active_jobs: int = 0


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
        #: Signature of the last *failed* profile load.  A malformed or
        #: schema-invalid profile is a persistent configuration error, and
        #: re-reading and re-validating the same file on every request
        #: would turn a fail-safe into request-path disk I/O.  The failure
        #: is cached by the same path+mtime signature, so it is retried
        #: only when the file actually changes.
        self._cached_profile_failure_signature: Optional[
            tuple[str, Optional[float]]
        ] = None

    # ── Profile loader ───────────────────────────────────────────────

    def _get_profile(self) -> Optional[CapacityProfile]:
        """Load the profile if configured.  Cached by path+mtime.

        Both the successful result **and** the failure are cached against
        the file's path and mtime.  A missing, unreadable, malformed, or
        schema-invalid profile is remembered so the same broken file is not
        re-parsed on every request; the cache is invalidated as soon as the
        file's signature changes.
        """
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
        if self._cached_profile_failure_signature == sig:
            # The same file already failed to load.  Do not re-read it.
            return None

        profile = safe_load_profile(path)
        self._cached_profile_signature = sig
        self._cached_profile = profile
        self._cached_profile_failure_signature = (
            None if profile is not None else sig
        )
        return profile

    def invalidate_profile_cache(self) -> None:
        """Force the next call to reload from disk."""
        self._cached_profile_signature = None
        self._cached_profile = None
        self._cached_profile_failure_signature = None

    def context_budgets(
        self, model_id: Optional[str] = None
    ) -> Optional["ContextBudgets"]:
        """Per-model context budgets for *model_id*, if any.

        The lookup is keyed by the **request's** model, not by the
        process-wide ``WHOOSHD_CAPACITY_MODEL_ID``.  A broker that serves
        several models must not apply model A's hard ceiling to a request
        routed to model B — that is a mis-constraint, not a policy.  A
        request whose model has no measured budgets is admitted unchanged
        (an unmeasured model is never silently constrained).

        When *model_id* is ``None`` the caller has not resolved a model, so
        the configured controller identity is used instead.
        """
        profile = self._get_profile()
        if profile is None:
            return None
        budgets = getattr(profile, "context_budgets", None)
        if not isinstance(budgets, ContextBudgets):
            return None

        if model_id is None:
            configured = get_capacity_model_id()
            # The operator's declared identity and the profile's model id
            # are compared the same way a request's model is: the same
            # relation, so a declared repository id and a profile recorded
            # against its local snapshot are recognised as one model, while
            # two repositories sharing a basename are not.
            if not configured or not model_identity_matches(
                profile.model_id, configured
            ):
                return None
        elif not model_identity_matches(profile.model_id, model_id):
            return None

        eligible, _reason = self._host_status(profile)
        if not eligible:
            return None
        return budgets

    @staticmethod
    def _host_status(
        profile: CapacityProfile,
    ) -> tuple[bool, Optional[str]]:
        """Whether *profile* was captured on a host this process may use.

        This is the machine/runtime half of profile eligibility.  The
        model half is deliberately separate: context budgets are selected
        by the request's model, while the calibrated *concurrency* is
        selected by the operator's configured identity.
        """
        runtime = get_capacity_runtime()
        machine_class = get_capacity_machine_class()
        if not runtime or not machine_class:
            return False, "identity_unavailable"
        if profile.runtime != runtime:
            return False, "runtime_mismatch"
        if profile.machine_class != machine_class:
            return False, "machine_mismatch"

        current_memory = CapacityController._host_memory_bytes()
        if (
            profile.host_memory_bytes is not None
            and current_memory is not None
            and profile.host_memory_bytes != current_memory
        ):
            return False, "host_memory_mismatch"
        if profile.host_memory_bytes is not None and current_memory is None:
            return False, "host_memory_unavailable"
        return True, None

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
        eligible, reason = self._host_status(profile)
        if not eligible:
            return profile, False, reason
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
        """Return the global active concurrency limit, taking mode and
        pressure into account.

        Always ``>= 1``.  Adaptive mode never exceeds the operator
        ceiling.

        This is the **global** limit only.  A lane's own concurrency
        ceiling is a separate constraint on a separate counter; the two
        are not interchangeable and must not be combined with ``min``.
        See :meth:`evaluate` for how both are applied.
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

        # -- Route-specific capacity reservation ------------------------
        # The lane that will execute this request may be able to run fewer
        # requests concurrently than the broker's global limit allows.
        # Capping here means the broker queues rather than admitting work
        # that would stall at the lane's own semaphore and be refused.
        lane_limit = ctx.lane_max_concurrent
        lane_active = ctx.lane_active_jobs
        lane_fields: dict[str, Any] = {
            "lane": ctx.lane,
            "lane_max_concurrent": lane_limit,
            "lane_active_jobs": lane_active,
            "lane_limited": False,
        }
        # -- Per-model context policy ------------------------------------
        # The context decision is taken before any capacity decision so a
        # request that cannot fit the model is rejected for the right
        # reason rather than queued behind work it can never fit.
        context_fields: dict[str, Any] = {}
        if ctx.context_budgets is not None:
            from whooshd.capacity_context import (
                RequestCost,
                evaluate_context,
            )

            estimated = ctx.requested_estimated_tokens or 0
            if ctx.request_cost is not None:
                cost = ctx.request_cost
            else:
                cost = RequestCost(
                    input_tokens=0,
                    output_tokens=0,
                    multimodal_tokens=0,
                    total_tokens=estimated,
                    input_is_exact=False,
                    multimodal_is_exact=False,
                    output_is_exact=False,
                )
            context_eval = evaluate_context(cost, ctx.context_budgets)
            context_fields = {
                "context_reason": context_eval.reason.value,
                "context_budget": (
                    context_eval.budget.value if context_eval.budget else None
                ),
                "context_total_tokens": context_eval.total_tokens,
                "context_breached_budget": (
                    context_eval.breached_budget.value
                    if context_eval.breached_budget
                    else None
                ),
            }
            if context_eval.over_limit:
                return CapacityEvaluation(
                    action=CapacityAction.REJECT,
                    effective_active_limit=self.effective_active_limit(
                        memory_pressure=ctx.memory_pressure,
                    ),
                    operator_ceiling=operator_ceiling,
                    mode=mode,
                    reason=CapacityDecisionReason.CONTEXT_OVERFLOW,
                    memory_pressure=ctx.memory_pressure,
                    **profile_fields,
                    **context_fields,
                    **lane_fields,
                )

        if not ctx.runtime_ready:
            return CapacityEvaluation(
                action=CapacityAction.REJECT,
                effective_active_limit=self.effective_active_limit(
                    memory_pressure=ctx.memory_pressure,
                ),                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=CapacityDecisionReason.RUNTIME_NOT_READY,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
                **context_fields,
                **lane_fields,
            )

        limit = self.effective_active_limit(
            memory_pressure=ctx.memory_pressure,
        )

        # -- Two independent constraints ---------------------------------
        # Global capacity and lane capacity are different budgets over
        # different counters.  Both must hold for a request to run:
        #
        #   global:  active_jobs        < global_limit
        #   lane:    lane_active_jobs   < lane_max_concurrent
        #
        # Combining them with min() and comparing against the global
        # counter is wrong in both directions: it lets a saturated lane
        # start work when the global budget has room, and it starves an
        # idle lane because some *other* lane is busy.
        global_available = ctx.active_jobs < limit
        lane_available = True
        if ctx.lane_max_concurrent is not None:
            lane_available = ctx.lane_active_jobs < max(
                int(ctx.lane_max_concurrent), 1
            )

        # Decide: can we run?
        if global_available and lane_available:
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
                    **context_fields,
                    **lane_fields,
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
                **context_fields,
                **lane_fields,
            )

        # This request cannot run now.  The queue holds work that could
        # not run at the time it arrived, so this request belongs at the
        # back of it — unless the lane is the binding constraint, in which
        # case that is the more useful reason to report.
        queue_has_room = ctx.queue_depth < ctx.max_queue_depth

        if not lane_available:
            lane_fields["lane_limited"] = True
            return CapacityEvaluation(
                action=CapacityAction.QUEUE if queue_has_room else CapacityAction.REJECT,
                effective_active_limit=limit,
                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=CapacityDecisionReason.LANE_CAPACITY,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
                **context_fields,
                **lane_fields,
            )

        if not queue_has_room:
            return CapacityEvaluation(
                action=CapacityAction.REJECT,
                effective_active_limit=limit,
                operator_ceiling=operator_ceiling,
                mode=mode,
                reason=CapacityDecisionReason.QUEUE_FULL,
                memory_pressure=ctx.memory_pressure,
                **profile_fields,
                **context_fields,
                **lane_fields,
            )

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
            **context_fields,
            **lane_fields,
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

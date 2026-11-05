"""Tests for route-specific capacity reservation.

Global capacity and lane capacity are **separate constraints over separate
counters**.  Both must hold for a request to run:

* global: ``active_jobs < global_limit``
* lane:   ``lane_active_jobs < lane_max_concurrent``

Conflating them is wrong in both directions:

* comparing the *global* counter against ``min(global, lane)`` lets a
  saturated lane start work the global budget has room for;
* it also starves an **idle** lane because some *other* lane is busy.

Both constraints must be re-checked at initial admission **and** when a
queued request is released.
"""

from __future__ import annotations

import pytest

from whooshd.admission import active_capacity_available
from whooshd.capacity_controller import (
    CapacityAction,
    CapacityContext,
    CapacityController,
    CapacityDecisionReason,
)
from whooshd.runtime import RuntimeState


def _ctx(**kwargs) -> CapacityContext:
    base = dict(
        active_jobs=0,
        queue_depth=0,
        max_queue_depth=8,
        runtime_ready=True,
    )
    base.update(kwargs)
    return CapacityContext(**base)


# ── The two constraints are independent ────────────────────────────────────


class TestGlobalAndLaneConstraintsAreIndependent:
    def test_global_limit_is_not_capped_by_the_lane(self):
        """The lane ceiling must not lower the *global* limit."""
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=1, lane_active_jobs=0)
        )
        assert result.effective_active_limit == controller.operator_ceiling
        # The lane ceiling is reported separately.
        assert result.lane_max_concurrent == 1

    def test_saturated_lane_with_spare_global_capacity_does_not_run(self):
        """Reviewer case 1.

        Global capacity 2, MLX-VLM lane capacity 1, one MLX-VLM request
        already running.  The second MLX-VLM request must not be admitted
        even though the global budget has room.
        """
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(
                active_jobs=1,
                lane="mlx_vlm",
                lane_max_concurrent=1,
                lane_active_jobs=1,
            )
        )
        assert result.action != CapacityAction.RUN
        assert result.action == CapacityAction.QUEUE
        assert result.reason == CapacityDecisionReason.LANE_CAPACITY
        assert result.lane_limited is True

    def test_idle_second_lane_runs_while_another_lane_is_busy(self):
        """Reviewer case 2.

        Global capacity 2, one MLX-VLM request active, and an idle lane
        with its own ceiling of 1.  The idle lane must be allowed to run.
        """
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(
                active_jobs=1,
                lane="llama_cpp",
                lane_max_concurrent=1,
                lane_active_jobs=0,
            )
        )
        assert result.action == CapacityAction.RUN
        assert result.lane_limited is False

    def test_both_lanes_busy_but_global_has_room_still_queues(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(
                active_jobs=1,
                lane="mlx_vlm",
                lane_max_concurrent=1,
                lane_active_jobs=1,
            )
        )
        assert result.action == CapacityAction.QUEUE

    def test_a_lane_without_a_ceiling_is_unaffected(self):
        controller = CapacityController()
        result = controller.evaluate(_ctx(lane="stub", lane_max_concurrent=None))
        assert result.action == CapacityAction.RUN
        assert result.lane_max_concurrent is None

    def test_operator_ceiling_still_bounds_the_global_limit(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=99, lane_active_jobs=0)
        )
        assert result.effective_active_limit == controller.operator_ceiling


# ── Queue release re-checks the lane ──────────────────────────────────────


class TestQueueReleaseChecksTheLane:
    def test_release_is_withheld_when_the_lane_is_saturated(self):
        """A queued request must not be released into a saturated lane."""
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False, lane="mlx_vlm")

        # Global capacity 2, one active job -> global has room.
        assert rt.active_jobs == 1
        assert (
            active_capacity_available(rt, lane="mlx_vlm", lane_max_concurrent=1)
            is False
        )

    def test_release_is_allowed_for_an_idle_lane(self):
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False, lane="mlx_vlm")

        # A different lane, idle, with its own ceiling of 1.
        assert (
            active_capacity_available(rt, lane="llama_cpp", lane_max_concurrent=1)
            is True
        )

    def test_release_is_withheld_when_global_capacity_is_gone(self):
        rt = RuntimeState()
        for _ in range(2):
            rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        assert (
            active_capacity_available(rt, lane="llama_cpp", lane_max_concurrent=4)
            is False
        )

    def test_lane_release_is_independent_of_the_lane_argument(self):
        """Without a lane ceiling the global rule applies unchanged."""
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        assert active_capacity_available(rt) is True


# ── Per-lane active counts ────────────────────────────────────────────────


class TestPerLaneActiveCounts:
    def test_lane_counts_are_independent(self):
        rt = RuntimeState()
        a = rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        rt.begin_request(model="m", stream=False, lane="llama_cpp")

        assert rt.active_jobs_for_lane("mlx_vlm") == 1
        assert rt.active_jobs_for_lane("llama_cpp") == 1
        assert rt.active_jobs == 2

        rt.complete_request(a)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0
        assert rt.active_jobs_for_lane("llama_cpp") == 1

    def test_requests_without_a_lane_are_counted_against_no_lane(self):
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0
        assert rt.active_jobs_for_lane(None) == 0
        assert rt.active_jobs == 1

    def test_queued_requests_do_not_count_as_active_for_their_lane(self):
        from whooshd.contracts import RequestLifecycleState

        rt = RuntimeState()
        rid = rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        rt.mark_queued(rid)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0


# ── Snapshot ───────────────────────────────────────────────────────────────


class TestSnapshotCarriesTheLaneDecision:
    def test_snapshot_includes_lane_fields(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=1, lane_active_jobs=0)
        )
        snapshot = result.snapshot()
        assert snapshot["lane"] == "mlx_vlm"
        assert snapshot["lane_max_concurrent"] == 1
        assert snapshot["lane_active_jobs"] == 0
        assert snapshot["lane_limited"] is False

    def test_snapshot_has_no_request_content(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=1, lane_active_jobs=0)
        )
        data = str(result.snapshot())
        for forbidden in ("prompt", "messages", "content", "tokens", "kv"):
            assert forbidden not in data, f"leaked: {forbidden}"


# ── Adapter contract ───────────────────────────────────────────────────────


class TestAdapterDeclaresItsLaneCeiling:
    def test_mlx_vlm_declares_its_semaphore_limit(self):
        from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig

        adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))
        # The MLX-VLM lane defaults to one concurrent request.
        assert adapter.max_concurrent_requests == 1

    def test_stub_reports_no_lane_ceiling_through_the_router(self):
        """An adapter that declares no ceiling must resolve to None.

        A protocol default does not apply to concrete classes, so the
        router reads the attribute defensively.
        """
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.routing import RuntimeRouter

        router = RuntimeRouter()
        router.register(StubInferenceAdapter())
        resolution = router._resolution(
            StubInferenceAdapter(), "stub-model", "configured_stub"
        )
        assert resolution.lane == "stub"
        assert resolution.lane_max_concurrent is None

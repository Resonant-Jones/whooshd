"""Tests for route-specific capacity reservation.

The broker's global limit and the inference lane's own limit are two
different ceilings.  When the lane is stricter — as the MLX-VLM adapter is,
with a semaphore that defaults to one concurrent request — the broker must
respect the lane's ceiling at admission rather than admitting work that will
stall at the adapter and be refused.

These tests pin:

* the lane ceiling caps the effective limit without lowering the global
  limit for other lanes;
* a saturated lane queues (or rejects) instead of admitting;
* per-lane active counts are accurate and independent;
* a lane that declares no ceiling is unaffected;
* the global operator ceiling is still honoured.
"""

from __future__ import annotations

import pytest

from whooshd.capacity_controller import (
    CapacityAction,
    CapacityContext,
    CapacityController,
    CapacityDecisionReason,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage
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


class TestLaneCapLimitsTheEffectiveLimit:
    def test_lane_ceiling_caps_the_effective_limit(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=1, lane_active_jobs=0)
        )
        # Global ceiling is 2 by default; the lane allows 1.
        assert result.effective_active_limit == 1
        assert result.lane == "mlx_vlm"
        assert result.lane_max_concurrent == 1
        assert result.lane_limited is False

    def test_global_ceiling_is_not_lowered_for_other_lanes(self):
        """A strict lane must not constrain a different lane."""
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="llama_cpp", lane_max_concurrent=8, lane_active_jobs=0)
        )
        assert result.effective_active_limit == controller.operator_ceiling
        assert result.lane_max_concurrent == 8

    def test_lane_without_a_ceiling_is_unaffected(self):
        controller = CapacityController()
        result = controller.evaluate(_ctx(lane="stub", lane_max_concurrent=None))
        assert result.effective_active_limit == controller.operator_ceiling
        assert result.lane_max_concurrent is None

    def test_operator_ceiling_still_bounds_the_lane(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=99, lane_active_jobs=0)
        )
        assert result.effective_active_limit == controller.operator_ceiling


class TestSaturatedLaneIsNotAdmitted:
    def test_saturated_lane_queues_rather_than_admitting(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=1, lane_active_jobs=1)
        )
        assert result.action == CapacityAction.QUEUE
        assert result.reason == CapacityDecisionReason.LANE_CAPACITY
        assert result.lane_limited is True

    def test_saturated_lane_rejects_when_the_queue_is_full(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(
                lane="mlx_vlm",
                lane_max_concurrent=1,
                lane_active_jobs=1,
                queue_depth=8,
                max_queue_depth=8,
            )
        )
        assert result.action == CapacityAction.REJECT
        assert result.reason == CapacityDecisionReason.LANE_CAPACITY

    def test_a_free_lane_is_admitted(self):
        controller = CapacityController()
        result = controller.evaluate(
            _ctx(lane="mlx_vlm", lane_max_concurrent=2, lane_active_jobs=1)
        )
        assert result.action == CapacityAction.RUN
        assert result.lane_limited is False


class TestPerLaneActiveCounts:
    def _runtime(self) -> RuntimeState:
        return RuntimeState()

    def test_lane_counts_are_independent(self):
        rt = self._runtime()
        a = rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        rt.begin_request(model="m", stream=False, lane="llama_cpp")

        assert rt.active_jobs_for_lane("mlx_vlm") == 1
        assert rt.active_jobs_for_lane("llama_cpp") == 1
        assert rt.active_jobs == 2

        rt.complete_request(a)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0
        assert rt.active_jobs_for_lane("llama_cpp") == 1

    def test_requests_without_a_lane_are_counted_against_no_lane(self):
        rt = self._runtime()
        rt.begin_request(model="m", stream=False)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0
        assert rt.active_jobs_for_lane(None) == 0
        assert rt.active_jobs == 1

    def test_queued_requests_do_not_count_as_active_for_their_lane(self):
        from whooshd.contracts import RequestLifecycleState

        rt = self._runtime()
        rid = rt.begin_request(model="m", stream=False, lane="mlx_vlm")
        rt.mark_queued(rid)
        assert rt.active_jobs_for_lane("mlx_vlm") == 0


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

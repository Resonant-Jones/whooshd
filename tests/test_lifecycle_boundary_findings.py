"""Regression tests for the four findings raised against ``e686771``.

These are the lifecycle-boundary defects the earlier corrections exposed:

1. **P1 — media tokens bypassed the input-token ceiling.**
   ``max_input_tokens`` was compared with the textual/structured input
   only.  A request with 1 000 text tokens and a 5 000-token image was
   admitted against a 4 096-token input maximum whenever its combined
   total stayed below the hard ceiling.

2. **P2 — granted reservations inflated queue depth.**
   A request removed from the scheduler queue by a cross-waiter grant
   remained ``QUEUED`` while holding a reservation, so
   ``RuntimeState.queue_depth`` over-reported occupancy and a new request
   could be refused as *queue full* while the real queue had room.

3. **P2 — the experiment's upstream probe lacked response validation.**
   A JSON error envelope with a 4xx/5xx status, or a 2xx payload without
   usage data, was still recorded as an *available* probe with absent
   counters.

4. **P2 — cross-waiter grants could exceed queue deadlines.**
   A candidate selected on behalf of another waiter was granted even when
   its own queue deadline had already passed, so it executed after the
   timeout it was supposed to be bounded by.

The tests are hermetic: no network, no Metal, no live broker.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from whooshd.capacity_context import (
    evaluate_context,
    estimate_request_cost,
)
from whooshd.capacity_profile import ContextBudgets, ContextDecisionReason
from whooshd.contracts import ChatCompletionRequest, ChatMessage
from whooshd.queue import QueueEntry, RequestQueue
from whooshd.runtime import RuntimeState

_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import lane_concurrency_experiment as harness  # noqa: E402


def _multimodal_request(media_parts: int = 1) -> ChatCompletionRequest:
    parts: list[dict] = [{"type": "text", "text": "x" * 4000}]
    for _ in range(media_parts):
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}})
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content=parts)],
        max_tokens=64,
    )


def _entry(request_id: str) -> QueueEntry:
    return QueueEntry(
        request_id=request_id,
        request=ChatCompletionRequest(
            model="m",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=64,
        ),
        lane="mlx_vlm",
        lane_max_concurrent=1,
    )


# ── Finding 1 — media tokens count toward the input ceiling ────────────────


class TestMediaTokensCountTowardTheInputCeiling:
    def test_an_image_heavy_request_is_refused_against_the_input_maximum(self):
        """The reviewer's scenario.

        1 000 text tokens and a 5 000-token image against a 4 096-token
        input maximum.  The combined *input* is 6 000 tokens, so the
        request must be refused even though its total sits below the hard
        ceiling.
        """
        budgets = ContextBudgets(
            hard_max_tokens=32000,
            recommended_max_tokens=16000,
            max_input_tokens=4096,
        )
        cost = estimate_request_cost(
            _multimodal_request(media_parts=1),
            multimodal_token_estimate=5000,
        )
        assert cost.multimodal_tokens == 5000
        assert cost.input_tokens < 4096, "the textual part alone is under the cap"

        result = evaluate_context(cost, budgets)
        assert result.reason == ContextDecisionReason.ABOVE_INPUT_LIMIT
        assert result.over_limit is True
        assert result.within_budget is False
        assert result.breached_budget is not None

    def test_a_request_under_the_combined_input_maximum_is_admitted(self):
        budgets = ContextBudgets(
            hard_max_tokens=32000, max_input_tokens=8192
        )
        cost = estimate_request_cost(
            _multimodal_request(media_parts=1),
            multimodal_token_estimate=256,
        )
        result = evaluate_context(cost, budgets)
        assert result.over_limit is False
        assert result.within_budget is True

    def test_media_without_an_estimate_is_still_unknown_not_free(self):
        """An unpriced image must not slip past the ceiling as zero tokens.

        The comparison is best-effort when the media cost is unknown, but
        an unknown cost is never treated as *within* the limit just
        because the known part is small.
        """
        budgets = ContextBudgets(hard_max_tokens=32000, max_input_tokens=4096)
        cost = estimate_request_cost(_multimodal_request(media_parts=1))
        assert cost.has_unknown_cost is True
        assert cost.cost_status == "unknown"
        # The known portion is under the ceiling, so the request is not
        # rejected — but the evaluation says the comparison is not exact.
        result = evaluate_context(cost, budgets)
        assert result.cost_is_exact is False
        assert result.cost_status == "unknown"


# ── Finding 2 — granted reservations do not inflate queue depth ───────────


class TestGrantedReservationsDoNotInflateQueueDepth:
    def test_a_granted_request_is_no_longer_counted_as_queued(self):
        runtime = RuntimeState()
        request_id = runtime.begin_request(
            model="m", stream=False, lane="mlx_vlm"
        )
        runtime.mark_queued(request_id)
        assert runtime.queue_depth == 1

        # The scheduler granted it: it has left the queue and holds a
        # reservation, even though its lifecycle record has not yet left
        # the QUEUED state.
        runtime.reserve_capacity(request_id, "mlx_vlm")
        assert runtime.queue_depth == 0
        assert runtime.oldest_queued_age_ms == 0.0

        # Once the reservation is released the record's own state governs.
        runtime.release_capacity(request_id)
        assert runtime.queue_depth == 1

    def test_other_queued_requests_are_still_counted(self):
        runtime = RuntimeState()
        granted = runtime.begin_request(model="m", stream=False, lane="mlx_vlm")
        runtime.mark_queued(granted)
        waiting = runtime.begin_request(model="m", stream=False, lane="mlx_vlm")
        runtime.mark_queued(waiting)

        runtime.reserve_capacity(granted, "mlx_vlm")
        assert runtime.queue_depth == 1

    def test_a_granted_request_still_holds_its_capacity_slot(self):
        """Excluding it from queue depth must not free its slot."""
        runtime = RuntimeState()
        request_id = runtime.begin_request(
            model="m", stream=False, lane="mlx_vlm"
        )
        runtime.mark_queued(request_id)
        runtime.reserve_capacity(request_id, "mlx_vlm")

        assert runtime.queue_depth == 0
        assert runtime.active_jobs == 1
        assert runtime.active_jobs_for_lane("mlx_vlm") == 1


# ── Finding 3 — the upstream probe validates its response ─────────────────


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def post(self, *args, **kwargs):
        return self._response


class TestUpstreamProbeValidatesItsResponse:
    @pytest.mark.asyncio
    async def test_an_http_error_envelope_is_not_available(self):
        client = _FakeClient(
            _FakeResponse(500, {"error": {"message": "boom"}})
        )
        result = await harness.upstream_probe(client)
        assert result["available"] is False
        assert "failure_reason" in result

    @pytest.mark.asyncio
    async def test_a_two_hundred_without_usage_is_not_available(self):
        client = _FakeClient(_FakeResponse(200, {"choices": []}))
        result = await harness.upstream_probe(client)
        assert result["available"] is False
        assert result.get("failure_reason") == "missing_usage"

    @pytest.mark.asyncio
    async def test_an_unparsable_body_is_not_available(self):
        client = _FakeClient(_FakeResponse(200, ValueError("not json")))
        result = await harness.upstream_probe(client)
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_a_valid_probe_is_available(self):
        client = _FakeClient(
            _FakeResponse(
                200,
                {
                    "usage": {
                        "prompt_tokens": 365,
                        "prompt_tokens_details": {"cached_tokens": 349},
                    },
                    "timings": {
                        "prompt_ms": 283.5,
                        "predicted_n": 74,
                        "predicted_per_second": 7.49,
                    },
                },
            )
        )
        result = await harness.upstream_probe(client)
        assert result["available"] is True
        assert result["prompt_tokens"] == 365
        assert result["cached_prompt_tokens"] == 349
        assert result["prefill_ms"] == 283.5
        # The upstream reports decode speed as ``predicted_per_second``.
        assert result["generation_tps"] == 7.49


# ── Finding 4 — grants honour the candidate's own queue deadline ──────────


class TestGrantsHonourTheCandidatesOwnDeadline:
    def test_an_expired_candidate_is_not_selected(self):
        queue = RequestQueue()
        entry = _entry("expired")
        queue.enqueue(entry)
        # The request has already waited longer than the queue timeout.
        entry.deadline_at = time.monotonic() - 1.0

        selected = queue.select_and_dequeue(capacity_available=lambda: True)
        assert selected is None
        assert queue.depth == 1, "the expired entry is not silently dropped"

    def test_a_live_candidate_is_still_selected(self):
        queue = RequestQueue()
        entry = _entry("live")
        queue.enqueue(entry)

        selected = queue.select_and_dequeue(capacity_available=lambda: True)
        assert selected is not None
        assert selected.request_id == "live"

    def test_a_deadline_is_stamped_at_enqueue_time(self):
        queue = RequestQueue()
        entry = _entry("stamped")
        assert entry.deadline_at is None
        queue.enqueue(entry)
        assert entry.deadline_at is not None
        assert entry.deadline_at > time.monotonic()

    @pytest.mark.asyncio
    async def test_a_late_grant_is_refused_by_its_owner(self):
        """A grant that arrives after the deadline must not execute."""
        queue = RequestQueue()
        entry = _entry("late")
        queue.enqueue(entry)
        # Another waiter granted it, but the request's own deadline has
        # already passed by the time its owner wakes.
        entry.deadline_at = time.monotonic() - 0.001
        entry.mark_granted()

        ready = await queue.wait_for_execution(
            entry, capacity_available=lambda: True
        )
        assert ready is False

    @pytest.mark.asyncio
    async def test_a_grant_made_in_time_is_honoured(self):
        queue = RequestQueue()
        entry = _entry("in-time")
        queue.enqueue(entry)
        entry.mark_granted()

        ready = await queue.wait_for_execution(
            entry, capacity_available=lambda: True
        )
        assert ready is True

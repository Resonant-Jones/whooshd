"""End-to-end regression tests for lane-aware admission.

The controller tests prove the *decisions* individually.  These prove the
complete **admission → queue → release → execution** contract, which is where
the two original defects lived:

1. A request for a **saturated lane** was admitted (or released from the
   queue) on the strength of spare *global* capacity alone, and then stalled
   at the adapter's semaphore.
2. An **idle second lane** was queued because another lane was busy — the
   global counter was being compared against the lane's ceiling.

The scenario mirrors the real deployment: global capacity 2, one lane with a
ceiling of 1, a second lane with a ceiling of 1.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.adapters.base import InferenceAdapter
from whooshd.contracts import ChatCompletionResponse, ChatCompletionUsage
from whooshd.runtime import RuntimeState


class _LaneAdapter:
    """Minimal adapter that declares a lane ceiling."""

    def __init__(self, kind: str, lane_limit: int | None, gate: asyncio.Event | None = None):
        self._kind = kind
        self._lane_limit = lane_limit
        self._gate = gate
        self.started = 0

    @property
    def name(self) -> str:
        return self._kind

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def max_concurrent_requests(self) -> int | None:
        return self._lane_limit

    @property
    def supports_streaming(self) -> bool:
        return False

    def is_loaded(self) -> bool:
        return True

    def model_id(self) -> str | None:
        return f"{self._kind}-model"

    async def warmup(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def health(self):  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def chat_completion(
        self, request, context=None
    ) -> ChatCompletionResponse:
        self.started += 1
        if self._gate is not None:
            await self._gate.wait()
        return ChatCompletionResponse(
            id=f"chatcmpl-{self._kind}",
            created=0,
            model=f"{self._kind}-model",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
        )

    async def chat_completion_stream(self, request, context=None):  # pragma: no cover
        raise NotImplementedError
        yield  # pragma: no cover

    async def generate(self, request, context=None):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def lane_deployment(monkeypatch):
    """A router with two lanes: one with a ceiling of 1, one idle."""
    from whooshd import app as app_module
    from whooshd.routing import RuntimeRouter

    gate = asyncio.Event()
    lane_a = _LaneAdapter("lane_a", 1, gate=gate)
    lane_b = _LaneAdapter("lane_b", 1)

    router = RuntimeRouter()
    router.register(lane_a)
    router.register(lane_b)
    monkeypatch.setattr(app_module, "get_router", lambda: router)

    runtime = RuntimeState()
    monkeypatch.setattr(app_module, "get_runtime", lambda: runtime)

    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "2")

    return {
        "router": router,
        "runtime": runtime,
        "lane_a": lane_a,
        "lane_b": lane_b,
        "gate": gate,
    }


class TestSaturatedLaneIsNotAdmittedDespiteSpareGlobalCapacity:
    @pytest.mark.asyncio
    async def test_second_request_to_a_busy_lane_queues(self, lane_deployment):
        """Global has room, the lane does not: the request must queue."""
        runtime = lane_deployment["runtime"]
        lane_a = lane_deployment["lane_a"]
        gate = lane_deployment["gate"]

        # One request already occupying lane_a.
        runtime.begin_request(model="lane_a-model", stream=False, lane="lane_a")
        occupying = next(iter(runtime._requests))
        assert runtime.active_jobs_for_lane("lane_a") == 1
        assert runtime.active_jobs == 1

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "lane_a-model",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    timeout=30,
                )
            )
            # Let the request reach the queue.
            await asyncio.sleep(0.25)

            # It must be queued, not executed.
            assert runtime.queue_depth == 1
            assert lane_a.started == 0
            assert not task.done()

            # The occupying request finishes: the lane frees up and the
            # adapter is allowed to proceed.
            runtime.complete_request(occupying)
            gate.set()
            resp = await asyncio.wait_for(task, timeout=20)

        assert resp.status_code == 200
        assert lane_a.started == 1
        assert runtime.queue_depth == 0


class TestIdleSecondLaneRunsWhileAnotherLaneIsBusy:
    @pytest.mark.asyncio
    async def test_request_to_an_idle_lane_runs_immediately(self, lane_deployment):
        """Another lane being busy must not starve an idle lane."""
        runtime = lane_deployment["runtime"]
        lane_b = lane_deployment["lane_b"]

        # lane_a is busy; lane_b is idle.
        runtime.begin_request(model="lane_a-model", stream=False, lane="lane_a")
        assert runtime.active_jobs == 1
        assert runtime.active_jobs_for_lane("lane_b") == 0

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "lane_b-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 200
        assert lane_b.started == 1
        # It ran directly rather than queueing.
        assert runtime.queue_depth == 0


class TestQueueReleaseRechecksTheLane:
    @pytest.mark.asyncio
    async def test_queued_request_is_not_released_while_the_lane_is_busy(
        self, lane_deployment
    ):
        """A queued request must wait for its own lane, not for global room."""
        runtime = lane_deployment["runtime"]
        lane_a = lane_deployment["lane_a"]
        gate = lane_deployment["gate"]

        runtime.begin_request(model="lane_a-model", stream=False, lane="lane_a")

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "lane_a-model",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    timeout=30,
                )
            )
            await asyncio.sleep(0.25)
            assert runtime.queue_depth == 1

            # The occupying request finishes, freeing global capacity, but
            # the lane is still saturated by the queued request's own lane
            # accounting until it is granted.
            runtime.complete_request(
                next(iter(runtime._requests))
            )

            # Give the queue a chance to (wrongly) release.
            await asyncio.sleep(0.4)
            assert not task.done(), "request must not run while its lane is busy"

            gate.set()
            resp = await asyncio.wait_for(task, timeout=20)

        assert resp.status_code == 200
        assert lane_a.started == 1


class TestGeneratePathIsLaneAware:
    @pytest.mark.asyncio
    async def test_generate_respects_the_lane_ceiling(self, lane_deployment):
        """``/v1/generate`` must not bypass the lane ceiling."""
        runtime = lane_deployment["runtime"]

        runtime.begin_request(model="lane_a-model", stream=False, lane="lane_a")

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/generate",
                json={"model_id": "lane_a-model", "prompt": "hi", "max_tokens": 4},
                timeout=30,
            )

        # The lane is saturated, so the request must be refused rather than
        # admitted to stall at the adapter.
        assert resp.status_code == 429
        assert resp.json()["code"] == "runner_overloaded"

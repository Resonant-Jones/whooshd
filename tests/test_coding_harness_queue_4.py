"""Coding harness profile test — 4 concurrent clients, 2 active, queue 4+.

This is Phase A of the throughput control plane.  It validates that:

  * 4 requests can be submitted concurrently.
  * 2 begin executing immediately (subject to WHOOSHD_MAX_ACTIVE_REQUESTS=2).
  * 2 wait internally via the queue (queue depth grows to 2).
  * All 4 eventually complete successfully.
  * active_jobs returns to 0.
  * queue_depth returns to 0.
  * No spurious 429 responses.
  * No cross-request contamination.
  * No stuck requests.

Active inference concurrency remains at 2 — Phase A proves that
queueing alone can satisfy 4-client harness workloads without
increasing backend execution pressure.

Validated against the stub runtime.  Real MLX validation is Phase E.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.admission import reset_controller_for_tests
from whooshd.app import app
from whooshd.config import get_max_active_requests
from whooshd.runtime import get_runtime


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """Force the canonical coding-harness profile for every test."""
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
    # Force the stub adapter to delay so we observe queue overlap.
    monkeypatch.setenv("WHOOSHD_STUB_RESPONSE_DELAY_SECONDS", "0.1")

    # Reset the queue singleton so each test gets a fresh asyncio.Event
    # bound to the current event loop.  Without this, the singleton's
    # event is bound to the first test's loop and subsequent tests
    # raise "bound to a different event loop".
    import whooshd.queue as qmod
    import whooshd.runtime as rmod
    qmod._queue = None
    rmod._runtime = None
    reset_controller_for_tests()
    yield
    qmod._queue = None
    rmod._runtime = None
    reset_controller_for_tests()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _chat(client: AsyncClient, body: dict):
    return await client.post("/v1/chat/completions", json=body)


class TestCodingHarnessQueue4:
    async def test_four_concurrent_clients_all_complete(self, client):
        """4 simultaneous chat completions all return 200 within timeout."""
        rt = get_runtime()
        # Reset to baseline.
        rt.complete_warmup()

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"Hello #{i}"}],
                "stream": True,
                "max_tokens": 16,
            }
            for i in range(4)
        ]

        # Fire all 4 in parallel.
        responses = await asyncio.gather(
            *[_chat(client, body) for body in bodies]
        )

        statuses = [r.status_code for r in responses]
        # All must succeed (no spurious 429).
        assert all(s == 200 for s in statuses), statuses

    async def test_no_spurious_429_when_queue_has_capacity(self, client):
        rt = get_runtime()
        rt.complete_warmup()

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"req {i}"}],
                "stream": False,
                "max_tokens": 16,
            }
            for i in range(4)
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])
        # No 429 from a queue that has capacity.
        assert sum(1 for r in responses if r.status_code == 429) == 0

    async def test_queue_depth_returns_to_zero(self, client):
        rt = get_runtime()
        rt.complete_warmup()
        max_active = get_max_active_requests()
        assert max_active == 2

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"req {i}"}],
                "stream": False,
                "max_tokens": 16,
            }
            for i in range(4)
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])

        # After completion, queue is empty and active jobs is 0.
        assert all(r.status_code == 200 for r in responses)
        assert rt.active_jobs == 0
        assert rt.queue_depth == 0

    async def test_counters_record_queued(self, client):
        rt = get_runtime()
        rt.complete_warmup()

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"req {i}"}],
                "stream": False,
                "max_tokens": 16,
            }
            for i in range(4)
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])
        assert all(r.status_code == 200 for r in responses)

        config = rt.build_admission_config()
        # At least 4 accepted; at least 2 queued (since only 2 can run
        # at once); at least 2 dequeued.
        assert config["counters"]["accepted"] >= 4
        assert config["counters"]["queued"] >= 2
        assert config["counters"]["dequeued"] >= 2

    async def test_eight_concurrent_clients(self, client):
        """Pushing past 4 — the queue should still admit up to max_queue_depth."""
        rt = get_runtime()
        rt.complete_warmup()

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"req {i}"}],
                "stream": False,
                "max_tokens": 8,
            }
            for i in range(8)
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])

        # All should succeed (max_queue_depth=8 with active=2 fits 8).
        statuses = [r.status_code for r in responses]
        assert all(s == 200 for s in statuses), statuses
        assert rt.active_jobs == 0
        assert rt.queue_depth == 0

    async def test_overflow_returns_clean_429(self, client, monkeypatch):
        """When queue is also full, return structured 429 (no crash)."""
        monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "2")  # smaller queue
        rt = get_runtime()
        rt.complete_warmup()

        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": f"req {i}"}],
                "stream": False,
                "max_tokens": 8,
            }
            for i in range(6)
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])
        statuses = [r.status_code for r in responses]
        # Some succeed, some get 429 — but never 5xx.
        assert all(s in (200, 429) for s in statuses), statuses
        # No crash, active returns to 0.
        assert rt.active_jobs == 0


class TestCodingHarnessQueue4CrossTalk:
    async def test_no_cross_talk_between_concurrent_requests(self, client):
        """Each request's response must reflect its own prompt only."""
        rt = get_runtime()
        rt.complete_warmup()

        # Mark each prompt with a unique sentinel.
        prompts = [
            f"SENTINEL-{i}-ONLY" for i in range(4)
        ]
        bodies = [
            {
                "model": "stub-model",
                "messages": [{"role": "user", "content": p}],
                "stream": False,
                "max_tokens": 32,
            }
            for p in prompts
        ]
        responses = await asyncio.gather(*[_chat(client, body) for body in bodies])
        for prompt, resp in zip(prompts, responses):
            assert resp.status_code == 200
            body = resp.json()
            # Stub echoes the prompt.  Verify only its own sentinel.
            content = body["choices"][0]["message"]["content"]
            for other in prompts:
                if other == prompt:
                    continue
                assert other not in content, (
                    f"cross-talk: prompt={prompt!r} saw {other!r} in response"
                )


class TestCapacitySnapshotDuringBurst:
    async def test_capacity_snapshot_reports_mode(self, client):
        rt = get_runtime()
        rt.complete_warmup()

        config = rt.build_admission_config()
        assert "capacity" in config
        assert config["capacity"]["capacity_mode"] in ("fixed", "adaptive")
        assert config["capacity"]["operator_ceiling"] == 2  # WHOOSHD_MAX_ACTIVE_REQUESTS
        assert config["capacity"]["effective_active_limit"] == 2
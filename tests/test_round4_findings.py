"""Regression tests for the five findings raised against ``fa771512``.

1. **P1 — a Hugging Face snapshot path broke model-identity matching.**
   ``_canonical_model_identity`` read the repository name out of the
   *path* below the cache directory, so ``models--org--name`` resolved to
   the repository ``org/name/snapshots/<revision>``.  A profile written
   against ``org/name`` then matched nothing and its hard context ceiling
   was silently never applied.

2. **P1 — the queue's selection-retry path could outlive the deadline.**
   When the front entry's deadline expired, ``select_and_dequeue``
   filtered it out and returned ``None``.  The retry branch then waited
   with a zero timeout and continued, skipping the timeout check, so the
   request spun in the queue forever instead of receiving its
   queue-timeout response.

3. **P2 — a 2xx response without token usage counted as a successful
   sample.**  The experiment could certify ``PASS`` on a band it never
   actually measured, and reported the absent token count as zero.

4. **P2 — unavailable swap counters did not affect the verdict.**  The
   experiment's thrash criterion could go unmeasured and the verdict was
   still ``PASS``.

5. **P2 — ``/v1/generate`` resolved the model a second time.**  The
   request was admitted against one resolved adapter and then executed on
   whichever adapter a second resolution produced, so the execution
   target was not necessarily the target whose capacity and context
   policy had been evaluated.

The tests are hermetic: no network, no Metal, no live broker.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.adapters.stub import StubInferenceAdapter
from whooshd.app import app
from whooshd.capacity_controller import (
    CapacityController,
    _canonical_model_identity,
    model_identity_matches,
)
from whooshd.capacity_profile import CapacityProfile, ContextBudgets
from whooshd.contracts import ChatCompletionRequest, ChatMessage, GenerateRequest
from whooshd.queue import QueueEntry, RequestQueue
from whooshd.routing import RuntimeRouter

_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import lane_concurrency_experiment as harness  # noqa: E402

from tests.test_lane_concurrency_experiment import (  # noqa: E402
    _FakeBroker,
    _install_fake_broker,
)


# ── Finding 1 — a snapshot path must still name its repository ─────────────


class TestHuggingFaceSnapshotIdentity:
    def test_snapshot_path_reduces_to_the_repository(self):
        """The reviewer's scenario, at the canonicalizer level."""

        repo, name = _canonical_model_identity(
            "/Users/ops/hf-cache/models--Qwen--Qwen2-VL-7B-Instruct/snapshots/abc123"
        )

        assert repo == "qwen/qwen2-vl-7b-instruct"
        assert name == "qwen2-vl-7b-instruct"

    def test_a_snapshot_path_matches_its_repository_id(self):
        assert (
            model_identity_matches(
                "Qwen/Qwen2-VL-7B-Instruct",
                "/Users/ops/hf-cache/models--Qwen--Qwen2-VL-7B-Instruct/snapshots/abc123",
            )
            is True
        )

    def test_the_cache_directory_is_the_identity_boundary(self):
        """Everything below the cache directory is layout, not identity."""

        for suffix in ("", "/snapshots/abc123", "/blobs/deadbeef", "/refs/main"):
            assert (
                model_identity_matches("org-a/foo", f"/models/models--org-a--foo{suffix}")
                is True
            )

    def test_a_snapshot_of_another_repository_is_still_not_a_match(self):
        """The round-3 guarantee survives the snapshot fix."""

        assert (
            model_identity_matches(
                "org-a/foo", "/models/models--org-b--foo/snapshots/abc123"
            )
            is False
        )

    def test_a_profile_ceiling_applies_to_a_snapshot_resolved_model(
        self, tmp_path, monkeypatch
    ):
        """End to end: the model's hard limit must actually be applied."""

        monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", "org-a/foo")
        monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "mlx_vlm")
        monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "mc")

        profile = CapacityProfile(
            model_id="org-a/foo",
            runtime="mlx_vlm",
            machine_class="mc",
            prompt_size_chars=10,
            configured_max_tokens=16,
            context_budgets=ContextBudgets(hard_max_tokens=100),
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json())

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()

        budgets = controller.context_budgets(
            model_id="/models/models--org-a--foo/snapshots/abc123"
        )

        assert budgets is not None
        assert budgets.hard_max_tokens == 100
        # A different repository's snapshot is still a different model.
        assert (
            controller.context_budgets(
                model_id="/models/models--org-b--foo/snapshots/abc123"
            )
            is None
        )


# ── Finding 2 — the queue deadline is not bypassed by a selection retry ─────


def _chat_req() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content="Hello")],
        max_tokens=64,
    )


class TestQueueDeadlineSurvivesSelectionRetry:
    @pytest.mark.asyncio
    async def test_an_expired_front_entry_returns_a_timeout_instead_of_spinning(
        self, monkeypatch
    ):
        """The reviewer's scenario.

        Capacity is available, but the entry's own deadline has passed, so
        the scheduler declines to select it.  The request must receive its
        queue-timeout response rather than retry forever.
        """

        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("WHOOSHD_QUEUE_POLL_INTERVAL_MS", "10")

        queue = RequestQueue()
        entry = QueueEntry(request_id="r-expired", request=_chat_req())
        queue.enqueue(entry)
        entry.deadline_at = time.monotonic() - 1.0

        granted = await asyncio.wait_for(
            queue.wait_for_execution(entry, capacity_available=lambda: True),
            timeout=5.0,
        )

        assert granted is False
        assert queue.depth == 0

    @pytest.mark.asyncio
    async def test_capacity_arriving_after_the_deadline_does_not_grant(
        self, monkeypatch
    ):
        """Capacity frees up only after the entry's own deadline passed."""

        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("WHOOSHD_QUEUE_POLL_INTERVAL_MS", "5")

        queue = RequestQueue()
        entry = QueueEntry(request_id="r-late", request=_chat_req())
        queue.enqueue(entry)
        entry.deadline_at = time.monotonic() + 0.05

        def capacity_available() -> bool:
            return time.monotonic() > entry.deadline_at

        started = time.monotonic()
        granted = await asyncio.wait_for(
            queue.wait_for_execution(entry, capacity_available=capacity_available),
            timeout=5.0,
        )

        assert granted is False
        assert time.monotonic() - started < 2.0
        assert queue.depth == 0

    @pytest.mark.asyncio
    async def test_a_live_front_entry_is_still_granted(self, monkeypatch):
        """The fix must not break the ordinary grant path."""

        monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "30")
        monkeypatch.setenv("WHOOSHD_QUEUE_POLL_INTERVAL_MS", "10")

        queue = RequestQueue()
        entry = QueueEntry(request_id="r-live", request=_chat_req())
        queue.enqueue(entry)

        granted = await asyncio.wait_for(
            queue.wait_for_execution(entry, capacity_available=lambda: True),
            timeout=5.0,
        )

        assert granted is True
        assert queue.depth == 0


# ── Finding 3 — a band without token usage is not a measurement ─────────────


def _collect_band_unverified(bands: list[dict]) -> list[str]:
    """The harness must be able to *report* missing band evidence.

    Calling it through a lookup keeps an implementation without the
    reporter a readable assertion failure rather than an AttributeError.
    """

    reporter = getattr(harness, "collect_band_unverified", None)
    assert reporter is not None, (
        "the harness must report evidence the bands did not obtain"
    )
    return reporter(bands)


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self) -> Any:
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body)


class _FakeClient:
    """Minimal client that always answers with the same payload."""

    def __init__(self, status_code: int, body: Any) -> None:
        self._status_code = status_code
        self._body = body

    async def post(self, url, json=None, timeout=None):  # noqa: A002
        return _FakeResponse(self._status_code, self._body)

    async def get(self, url, timeout=None):
        return _FakeResponse(200, {"active_jobs": 0, "queue_depth": 0})


class TestTokenEvidenceIsRequired:
    @pytest.mark.asyncio
    async def test_a_response_without_usage_is_not_a_successful_sample(self):
        record = await harness.one_request(
            _FakeClient(200, {"choices": [{"message": {"content": "hi"}}]}), 0
        )

        assert record["ok"] is False
        assert record["measurement_missing"] is True
        assert record["error"] == "missing_completion_token_usage"

    @pytest.mark.asyncio
    async def test_a_response_without_completion_tokens_is_not_a_sample(self):
        record = await harness.one_request(
            _FakeClient(200, {"usage": {"prompt_tokens": 10}}), 0
        )

        assert record["ok"] is False
        assert record["measurement_missing"] is True

    @pytest.mark.asyncio
    async def test_a_band_without_token_evidence_reports_unknown_throughput(
        self, monkeypatch
    ):
        monkeypatch.setattr(harness, "swap_counters", lambda: None)
        monkeypatch.setattr(harness, "mem_pressure", lambda: "unavailable")

        band = await harness.run_band(
            _FakeClient(200, {"choices": [{"message": {"content": "hi"}}]}), 2
        )

        assert band["token_evidence_available"] is False
        # An absent measurement is unknown, never zero.
        assert band["aggregate_tokens_per_second"] is None
        assert band["per_request_tokens_per_second"] is None
        assert band["unverified_count"] == 2

    def test_a_missing_measurement_is_unverified_rather_than_failed(self):
        bands = [
            {
                "concurrency": 2,
                "records": [
                    {"ok": False, "measurement_missing": True, "error": "missing_completion_token_usage"},
                    {"ok": False, "measurement_missing": True, "error": "missing_completion_token_usage"},
                ],
                "swap_out_delta_pages": 0,
                "unverified_count": 2,
            }
        ]

        assert harness.collect_band_failures(bands) == []
        reasons = _collect_band_unverified(bands)
        assert reasons
        assert any("completion-token" in reason for reason in reasons)

    def test_a_real_request_failure_is_still_a_failure(self):
        bands = [
            {
                "concurrency": 1,
                "records": [{"ok": False, "error": "http_status:500"}],
                "swap_out_delta_pages": 0,
                "unverified_count": 0,
            }
        ]

        failures = harness.collect_band_failures(bands)
        assert failures
        assert "http_status:500" in failures[0]
        assert _collect_band_unverified(bands) == []


# ── Finding 4 — missing swap evidence cannot be a clean run ────────────────


class TestSwapEvidenceIsRequired:
    def test_an_unavailable_swap_read_is_unverified(self):
        bands = [
            {
                "concurrency": 2,
                "records": [],
                "swap_out_delta_pages": None,
                "unverified_count": 0,
            }
        ]

        assert harness.collect_band_failures(bands) == []
        reasons = _collect_band_unverified(bands)
        assert reasons
        assert any("swap" in reason for reason in reasons)

    def test_a_measured_swap_read_is_not_unverified(self):
        bands = [
            {
                "concurrency": 2,
                "records": [],
                "swap_out_delta_pages": 0,
                "unverified_count": 0,
            }
        ]

        assert _collect_band_unverified(bands) == []

    def test_a_run_without_swap_evidence_is_not_certified_pass(
        self, monkeypatch, capsys
    ):
        broker = _FakeBroker()
        _install_fake_broker(monkeypatch, broker)
        monkeypatch.setattr(harness, "swap_counters", lambda: None)

        exit_code = asyncio.run(harness.main())
        report = json.loads(capsys.readouterr().out)

        assert report["verdict"] == harness.VERDICT_UNVERIFIED
        assert report["verdict"] != harness.VERDICT_PASS
        assert any("swap" in reason for reason in report["unverified"])
        assert exit_code == 2

    def test_a_run_without_token_evidence_is_not_certified_pass(
        self, monkeypatch, capsys
    ):
        broker = _FakeBroker(chat_payload={"choices": [{"message": {"content": "hi"}}]})
        _install_fake_broker(monkeypatch, broker)

        exit_code = asyncio.run(harness.main())
        report = json.loads(capsys.readouterr().out)

        assert report["verdict"] == harness.VERDICT_UNVERIFIED
        assert report["verdict"] != harness.VERDICT_PASS
        assert any(
            "completion-token" in reason for reason in report["unverified"]
        )
        assert exit_code == 2


# ── Finding 5 — /v1/generate executes the admitted resolution ──────────────


class _RecordingAdapter(StubInferenceAdapter):
    """A stub adapter that records whether it was the execution target."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name
        self.generate_calls = 0

    @property
    def name(self) -> str:
        return self._name

    def model_id(self):
        return f"{self._name}-model"

    async def generate(self, request):
        self.generate_calls += 1
        return await super().generate(request)


class TestGenerateExecutesTheAdmittedResolution:
    @pytest.mark.asyncio
    async def test_generate_executes_the_resolution_it_was_admitted_against(
        self, monkeypatch
    ):
        """The reviewer's scenario.

        The route resolves the model for admission, then executes.  If the
        execution resolves again, the request can run on a different
        adapter than the one whose limits were checked.
        """

        router = RuntimeRouter()
        admitted = _RecordingAdapter("admitted-adapter")
        other = _RecordingAdapter("other-adapter")

        resolutions = [
            router._resolution(admitted, "requested-model", "external_route"),
            router._resolution(other, "requested-model", "external_route"),
        ]
        resolve_calls: list[str] = []

        async def _resolve(model_id: str):
            resolve_calls.append(model_id)
            return resolutions[min(len(resolve_calls) - 1, len(resolutions) - 1)]

        monkeypatch.setattr(router, "resolve_model_runtime", _resolve)
        monkeypatch.setattr("whooshd.app.get_router", lambda: router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/generate",
                json={"prompt": "sentinel prompt", "model_id": "requested-model"},
            )

        assert response.status_code == 200
        assert admitted.generate_calls == 1
        assert other.generate_calls == 0
        # The model is resolved once, for admission.
        assert len(resolve_calls) == 1

    @pytest.mark.asyncio
    async def test_router_generate_still_resolves_when_no_resolution_is_given(
        self, monkeypatch
    ):
        """The router's own contract is unchanged for its other callers."""

        monkeypatch.setattr("whooshd.config.get_adapter_backend", lambda: "stub")
        router = RuntimeRouter()
        router.register(StubInferenceAdapter())

        generated = await router.generate(
            GenerateRequest(
                prompt="sentinel prompt",
                model_id="requested-model",
                request_id="generate-request-7",
            )
        )

        assert generated.ok is True
        assert generated.runtime.provenance is not None
        assert generated.runtime.provenance.adapter_name == "stub"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Regression tests for the CWC-013 lane-concurrency experiment harness.

Two confirmed defects are covered here:

1. **P1 — the configured lane was never verified.**  The harness read the
   broker's admission state, printed it, and never validated it.  A broker
   whose lane limit was still one could answer two concurrent requests with
   2xx each (serially), leaving no failures behind and producing a ``PASS``
   that certified concurrency two without ever testing it.

2. **P2 — cleanup was never verified.**  A failed or unreachable final
   admission read, and a nonzero ``active_jobs`` / ``queue_depth`` at rest,
   were recorded but never contributed to the verdict, so a leaking run
   could still be reported as ``PASS``.

The tests are hermetic: no network, no Metal, no live broker, no spawned
processes.  The broker is faked at the ``httpx.AsyncClient`` boundary and
the harness logic is exercised through its pure functions.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

# Import the experiment harness as a module.
# The script is at scripts/lane_concurrency_experiment.py
_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPT_DIR)

import lane_concurrency_experiment as harness  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class _FakeBroker:
    """A minimal fake of the experiment broker."""

    def __init__(
        self,
        *,
        admission_body=None,
        capacity_body=None,
        chat_payload=None,
        admission_unreachable: bool = False,
        active_at_rest: int = 0,
        queue_depth: int = 0,
        post_delay: float = 0.15,
    ):
        self.base = "http://broker.test"
        self.upstream = "http://upstream.test"
        self.admission_body = admission_body or {
            "max_active_requests": 2,
            "active_jobs": 0,
            "queue_enabled": True,
            "queue_depth": 0,
            "capacity": {"lane": None, "lane_max_concurrent": None},
        }
        self.capacity_body = capacity_body or {
            "effective_active_limit": 2,
            "operator_ceiling": 2,
            "lane": None,
            "lane_max_concurrent": None,
        }
        self.chat_payload = chat_payload or {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        self.admission_unreachable = admission_unreachable
        self.active_at_rest = active_at_rest
        self.queue_depth = queue_depth
        self.post_delay = post_delay
        self.in_flight = 0
        self.chat_posts = 0


class _FakeAsyncClient:
    """Fake ``httpx.AsyncClient`` bound to a :class:`_FakeBroker`."""

    broker: _FakeBroker

    def __init__(self, *args, **kwargs):
        self._broker = type(self).broker

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, timeout=None):
        broker = self._broker
        if broker.admission_unreachable:
            raise RuntimeError("connection refused")
        if url.endswith("/runtime/admission"):
            body = dict(broker.admission_body)
            # While requests are in flight the broker reports them as active.
            body["active_jobs"] = broker.in_flight + (
                broker.active_at_rest if broker.in_flight == 0 else 0
            )
            body["queue_depth"] = broker.queue_depth
            return _FakeResponse(200, body)
        if url.endswith("/runtime/capacity"):
            return _FakeResponse(200, dict(broker.capacity_body))
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url, json=None, timeout=None):
        broker = self._broker
        broker.in_flight += 1
        try:
            # A real request occupies the lane for a while; the harness's
            # admission sampler must be able to observe that window.
            await asyncio.sleep(broker.post_delay)
            if url.startswith(broker.base):
                broker.chat_posts += 1
                return _FakeResponse(200, dict(broker.chat_payload))
            return _FakeResponse(
                200, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            )
        finally:
            broker.in_flight -= 1


def _install_fake_broker(monkeypatch, broker: _FakeBroker) -> None:
    """Point the harness at a fake broker and neutralise host probes."""
    monkeypatch.setattr(harness, "BASE", broker.base)
    monkeypatch.setattr(harness, "UPSTREAM", broker.upstream)
    monkeypatch.setattr(harness, "EXPECTED_LANE_MAX_CONCURRENT_RAW", "2")
    monkeypatch.setattr(harness, "EXPECTED_LANE_RAW", None)
    monkeypatch.setattr(harness, "SAMPLE_INTERVAL", 0.02)
    # The thrash criterion is part of the verdict, so the fake host must
    # *report* swap counters — an unreadable counter is missing evidence,
    # not a clean run.
    monkeypatch.setattr(
        harness, "swap_counters", lambda: {"swapins": 0, "swapouts": 0}
    )
    monkeypatch.setattr(harness, "mem_pressure", lambda: "unavailable")
    monkeypatch.setattr(harness.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(_FakeAsyncClient, "broker", broker, raising=False)


# ── P1: lane attestation ───────────────────────────────────────────────────


class TestParseExpectedLaneLimit:
    """The operator declaration is parsed strictly; absence is never a value."""

    def test_absent_declaration_is_none(self):
        assert harness.parse_expected_lane_limit(None) is None

    def test_blank_declaration_is_none(self):
        assert harness.parse_expected_lane_limit("   ") is None

    def test_non_numeric_declaration_is_none(self):
        assert harness.parse_expected_lane_limit("two") is None

    def test_non_positive_declaration_is_none(self):
        assert harness.parse_expected_lane_limit("0") is None

    def test_valid_declaration_is_parsed(self):
        assert harness.parse_expected_lane_limit("2") == 2
        assert harness.parse_expected_lane_limit(" 3 ") == 3


class TestExtractLaneEvidence:
    """Lane evidence is read from broker payloads when — and only when — present."""

    def test_broker_level_snapshot_exposes_no_lane_ceiling(self):
        """The broker's capacity snapshot carries no request-specific ceiling."""
        payload = {
            "capacity": {"lane": None, "lane_max_concurrent": None},
            "active_jobs": 0,
        }
        evidence = harness.extract_lane_evidence({"admission": payload})
        assert evidence["lane_max_concurrent"] is None
        assert evidence["lane"] is None

    def test_admission_details_expose_lane_ceiling(self):
        payload = {
            "details": {
                "lane": "mlx_vlm",
                "lane_max_concurrent": 2,
                "lane_active_jobs": 1,
                "lane_limited": False,
            }
        }
        evidence = harness.extract_lane_evidence({"chat_completion_response": payload})
        assert evidence["lane_max_concurrent"] == 2
        assert evidence["lane"] == "mlx_vlm"
        assert "lane_max_concurrent" in evidence["lane_max_concurrent_source"]

    def test_null_values_are_not_treated_as_measurements(self):
        payload = {"lane": None, "lane_max_concurrent": None}
        evidence = harness.extract_lane_evidence({"admission": payload})
        assert evidence["lane_max_concurrent"] is None
        assert evidence["lane"] is None


class TestLaneAttestation:
    """P1: the lane configuration must be positively attested."""

    def _attest(self, **overrides):
        kwargs = dict(
            admission={"max_active_requests": 2, "queue_enabled": True},
            admission_reachable=True,
            expected_lane_max_concurrent=2,
            reported_lane_max_concurrent=None,
            reported_lane_max_concurrent_source=None,
            reported_lane=None,
            expected_lane=None,
            global_ceilings={"max_active_requests": 2},
            observation={"samples": 10, "peak_active_jobs": 2, "peak_queue_depth": 0},
        )
        kwargs.update(overrides)
        return harness.attest_lane_configuration(**kwargs)

    def test_missing_declaration_is_unverified(self):
        """Without a declared expectation the lane cannot be attested."""
        attestation = self._attest(expected_lane_max_concurrent=None)
        assert attestation.status == harness.VERDICT_UNVERIFIED
        assert attestation.attested is False
        assert any("expected lane ceiling" in reason for reason in attestation.unverified)

    def test_unreachable_broker_is_unverified(self):
        """An unreachable target broker is never a pass."""
        attestation = self._attest(admission=None, admission_reachable=False)
        assert attestation.status == harness.VERDICT_UNVERIFIED
        assert attestation.attested is False
        assert any("unreachable" in reason for reason in attestation.unverified)

    def test_broker_reported_lane_ceiling_mismatch_fails(self):
        """A broker whose lane limit is one must not certify concurrency two."""
        attestation = self._attest(
            reported_lane_max_concurrent=1,
            reported_lane_max_concurrent_source="chat_completion_response.details",
        )
        assert attestation.status == harness.VERDICT_FAILED
        assert any("lane ceiling" in reason for reason in attestation.failures)

    def test_broker_reported_lane_ceiling_match_attests(self):
        attestation = self._attest(
            reported_lane_max_concurrent=2,
            reported_lane_max_concurrent_source="chat_completion_response.details",
        )
        assert attestation.status == harness.ATTESTATION_ATTESTED
        assert attestation.attested is True

    def test_observed_two_concurrent_active_jobs_attests(self):
        """Without a broker report, the run itself must show two at once."""
        attestation = self._attest(
            observation={"samples": 10, "peak_active_jobs": 2, "peak_queue_depth": 0}
        )
        assert attestation.status == harness.ATTESTATION_ATTESTED

    def test_observed_serialization_fails(self):
        """A queued request during the x2 band means no concurrency two."""
        attestation = self._attest(
            observation={"samples": 10, "peak_active_jobs": 1, "peak_queue_depth": 1}
        )
        assert attestation.status == harness.VERDICT_FAILED
        assert any("queued" in reason for reason in attestation.failures)

    def test_inconclusive_observation_is_unverified_not_passed(self):
        """A missed observation is unknown, never a pass."""
        attestation = self._attest(
            observation={"samples": 1, "peak_active_jobs": 1, "peak_queue_depth": 0}
        )
        assert attestation.status == harness.VERDICT_UNVERIFIED
        assert attestation.attested is False

    def test_global_ceiling_below_two_fails(self):
        attestation = self._attest(global_ceilings={"max_active_requests": 1})
        assert attestation.status == harness.VERDICT_FAILED
        assert any("max_active_requests" in reason for reason in attestation.failures)

    def test_unreported_global_ceiling_is_unverified(self):
        attestation = self._attest(global_ceilings={"max_active_requests": None})
        assert attestation.status == harness.VERDICT_UNVERIFIED
        assert any("max_active_requests" in reason for reason in attestation.unverified)

    def test_preflight_without_observation_stays_pending(self):
        """Before the run there is nothing to fail on — the run still has to happen."""
        attestation = self._attest(observation=None)
        assert attestation.status == harness.ATTESTATION_PENDING
        assert attestation.failures == []
        assert attestation.unverified == []


# ── P2: cleanup verification ───────────────────────────────────────────────


class TestEvaluateCleanup:
    """P2: the final broker state must be verified, not assumed."""

    def test_unreachable_final_read_is_unverified(self):
        failures, unverified = harness.evaluate_cleanup(None, False)
        assert failures == []
        assert unverified
        assert any("unreachable" in reason for reason in unverified)

    def test_nonzero_active_jobs_is_a_failure(self):
        failures, unverified = harness.evaluate_cleanup(
            {"active_jobs": 2, "queue_depth": 0}, True
        )
        assert failures
        assert any("active_jobs" in reason for reason in failures)
        assert unverified == []

    def test_nonzero_queue_depth_is_a_failure(self):
        failures, unverified = harness.evaluate_cleanup(
            {"active_jobs": 0, "queue_depth": 1}, True
        )
        assert failures
        assert any("queue_depth" in reason for reason in failures)
        assert unverified == []

    def test_missing_counters_are_unverified_not_zero(self):
        """A missing counter is unknown — it must not read as clean."""
        failures, unverified = harness.evaluate_cleanup({}, True)
        assert failures == []
        assert len(unverified) == 2
        assert any("active_jobs" in reason for reason in unverified)
        assert any("queue_depth" in reason for reason in unverified)

    def test_clean_state_has_no_findings(self):
        failures, unverified = harness.evaluate_cleanup(
            {"active_jobs": 0, "queue_depth": 0}, True
        )
        assert failures == []
        assert unverified == []


class TestComposeVerdict:
    """The verdict distinguishes pass, failure and unknown."""

    def test_pass_requires_no_failures_and_no_unknowns(self):
        assert harness.compose_verdict([], []) == harness.VERDICT_PASS

    def test_measured_failure_beats_unknown(self):
        verdict = harness.compose_verdict(["left work behind"], ["unreachable"])
        assert verdict == harness.VERDICT_FAILED

    def test_missing_evidence_is_unverified(self):
        verdict = harness.compose_verdict([], ["final admission read unreachable"])
        assert verdict == harness.VERDICT_UNVERIFIED
        assert verdict != harness.VERDICT_PASS

    def test_exit_codes(self):
        assert harness.verdict_exit_code(harness.VERDICT_PASS) == 0
        assert harness.verdict_exit_code(harness.VERDICT_FAILED) == 1
        assert harness.verdict_exit_code(harness.VERDICT_UNVERIFIED) == 2


# ── End-to-end, against a faked broker ─────────────────────────────────────


def _run_main(capsys) -> tuple[int, dict]:
    exit_code = asyncio.run(harness.main())
    out = capsys.readouterr().out
    report = json.loads(out)
    return exit_code, report


class TestMainEndToEnd:
    """The harness as a whole must not certify an unverified lane."""

    def test_clean_attested_run_passes(self, monkeypatch, capsys):
        broker = _FakeBroker()
        _install_fake_broker(monkeypatch, broker)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_PASS
        assert report["failures"] == []
        assert report["unverified"] == []
        assert report["lane_attestation"]["status"] == harness.ATTESTATION_ATTESTED
        assert exit_code == 0

    def test_lane_ceiling_one_is_rejected(self, monkeypatch, capsys):
        """The original P1 scenario: the broker's lane limit is still one."""
        broker = _FakeBroker(
            chat_payload={
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "details": {
                    "lane": "mlx_vlm",
                    "lane_max_concurrent": 1,
                    "lane_active_jobs": 1,
                    "lane_limited": False,
                },
            }
        )
        _install_fake_broker(monkeypatch, broker)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_FAILED
        assert report["verdict"] != harness.VERDICT_PASS
        assert report["bands"] == []
        assert any("lane ceiling" in reason for reason in report["failures"])
        assert exit_code == 1

    def test_unreachable_broker_is_unverified(self, monkeypatch, capsys):
        broker = _FakeBroker(admission_unreachable=True)
        _install_fake_broker(monkeypatch, broker)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_UNVERIFIED
        assert report["verdict"] != harness.VERDICT_PASS
        assert report["unverified"]
        assert exit_code == 2

    def test_missing_lane_declaration_aborts_unverified(self, monkeypatch, capsys):
        """No operator declaration means the lane cannot be attested at all."""
        broker = _FakeBroker()
        _install_fake_broker(monkeypatch, broker)
        monkeypatch.setattr(harness, "EXPECTED_LANE_MAX_CONCURRENT_RAW", None)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_UNVERIFIED
        assert report["bands"] == []
        assert any("expected lane ceiling" in reason for reason in report["unverified"])
        assert exit_code == 2

    def test_leftover_work_at_rest_fails_the_run(self, monkeypatch, capsys):
        """The original P2 scenario: the run leaves work behind."""
        broker = _FakeBroker(active_at_rest=1)
        _install_fake_broker(monkeypatch, broker)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_FAILED
        assert any("active_jobs" in reason for reason in report["failures"])
        assert report["cleanup"]["active_jobs_at_rest"] == 1
        assert exit_code == 1

    def test_nonzero_queue_depth_at_rest_fails_the_run(self, monkeypatch, capsys):
        broker = _FakeBroker(queue_depth=1)
        _install_fake_broker(monkeypatch, broker)

        exit_code, report = _run_main(capsys)

        assert report["verdict"] == harness.VERDICT_FAILED
        assert any("queue_depth" in reason for reason in report["failures"])
        assert exit_code == 1

    def test_warm_up_swap_accounting_is_exercised(self, monkeypatch, capsys):
        """The warm-up swap deltas must be computed without raising."""
        broker = _FakeBroker()
        _install_fake_broker(monkeypatch, broker)
        monkeypatch.setattr(
            harness, "swap_counters", lambda: {"swapins": 3, "swapouts": 0}
        )

        exit_code, report = _run_main(capsys)

        assert report["cold_start_observation"]["swap_out_delta_pages"] == 0
        assert report["cold_start_observation"]["swap_in_delta_pages"] == 0
        assert report["verdict"] == harness.VERDICT_PASS
        assert exit_code == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

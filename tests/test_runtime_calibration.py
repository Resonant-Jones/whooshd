"""Regression tests for the runtime calibration harness.

Seven confirmed defects are pinned here, one test class per defect:

1. **P1 — band concurrency.** A band named ``xN`` must exercise N
   simultaneous in-flight requests, not ``requests`` of them.
2. **P1 — markdown rendering.** An unavailable upstream cache probe must
   render without a ``KeyError``.
3. **P1 — terminal SSE marker.** A 2xx stream without ``[DONE]`` is a
   failure, not a success.
4. **P2 — queue latency.** Queue latency must be measured from broker
   lifecycle timestamps when the broker publishes them, and reported as
   explicitly unavailable otherwise — never silently null, never zero.
5. **P1 — cache probe validity.** A failed upstream probe must not be
   recorded as measured cache evidence.
6. **P2 — leak check.** An unreachable final leak check must be *unknown*,
   never ``leaked: false``.
7. **P1 — token-phase failures.** A band whose token-accounting phase
   failed must not be reported as successful.

Every test is hermetic: no network, no Metal, no broker.  The HTTP layer is
replaced by an ``httpx.MockTransport`` handler.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
import pytest

from whooshd.bench import runtime_calibration as rc


# ── Fake broker ────────────────────────────────────────────────────────────


class FakeBroker:
    """A minimal, hermetic stand-in for the Whoosh'd HTTP surface.

    Records every request it served so tests can assert on concurrency and
    on how many requests actually reached the broker.
    """

    def __init__(
        self,
        *,
        stream_body: Optional[str] = None,
        nonstream_status: int = 200,
        nonstream_body: Optional[dict[str, Any]] = None,
        stream_status: int = 200,
        admission_body: Optional[dict[str, Any]] = None,
        lifecycle: Optional[dict[str, dict[str, Any]]] = None,
        provenance_header: Optional[str] = None,
        include_done: bool = True,
        usage: Optional[dict[str, Any]] = None,
    ) -> None:
        self.stream_body = stream_body
        self.nonstream_status = nonstream_status
        self.nonstream_body = nonstream_body
        self.stream_status = stream_status
        self.admission_body = admission_body
        self.lifecycle = lifecycle or {}
        self.provenance_header = provenance_header
        self.include_done = include_done
        self.usage = usage if usage is not None else {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        }

        self.in_flight = 0
        self.max_in_flight = 0
        self.stream_calls = 0
        self.nonstream_calls = 0
        self.admission_calls = 0
        self.lifecycle_calls = 0

    # -- helpers ---------------------------------------------------------
    def _default_stream(self) -> str:
        chunks = [
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
        ]
        if self.include_done:
            chunks.append("data: [DONE]")
        return "\n\n".join(chunks) + "\n\n"

    def _default_nonstream(self) -> dict[str, Any]:
        if self.nonstream_body is not None:
            return self.nonstream_body
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": "local-chat",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "hi"}}
            ],
            "usage": self.usage,
        }

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/chat/completions":
            payload = json.loads(request.content or b"{}")
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(0.02)
                if payload.get("stream"):
                    self.stream_calls += 1
                    headers = {"X-Whoosh-Request-ID": "whoosh-fake"}
                    if self.provenance_header is not None:
                        headers["X-Whooshd-Runtime-Provenance"] = (
                            self.provenance_header
                        )
                    body = self.stream_body
                    if body is None:
                        body = self._default_stream()
                    return httpx.Response(
                        self.stream_status,
                        text=body,
                        headers=headers,
                    )
                self.nonstream_calls += 1
                headers = {"X-Whoosh-Request-ID": "whoosh-fake"}
                if self.provenance_header is not None:
                    headers["X-Whooshd-Runtime-Provenance"] = self.provenance_header
                return httpx.Response(
                    self.nonstream_status,
                    json=self._default_nonstream(),
                    headers=headers,
                )
            finally:
                self.in_flight -= 1
        if path == "/runtime/admission":
            self.admission_calls += 1
            body = self.admission_body
            if body is None:
                body = {
                    "active_jobs": 0,
                    "queue_depth": 0,
                    "max_queue_depth": 32,
                    "queue_enabled": True,
                    "counters": {"queued": 0, "dequeued": 0},
                }
            return httpx.Response(200, json=body)
        if path == "/runtime/requests":
            self.lifecycle_calls += 1
            return httpx.Response(
                200, json={"requests": list(self.lifecycle.values()), "active_count": 0}
            )
        return httpx.Response(404, json={"error": "not found"})


def _client(broker: FakeBroker) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(broker.handler))


async def _run_band(
    broker: FakeBroker,
    *,
    band: int,
    requests: int,
    upstream_url: Optional[str] = None,
    upstream_model: Optional[str] = None,
) -> rc.BandEvidence:
    async with _client(broker) as client:
        evidence, _ = await rc._run_band(
            client,
            base_url="http://broker.test",
            model="local-chat",
            band=band,
            requests=requests,
            prompt="hello",
            max_tokens=16,
            timeout=10.0,
            upstream_url=upstream_url,
            upstream_model=upstream_model,
        )
    return evidence


# ── Defect 1 — band concurrency ────────────────────────────────────────────


class TestBandConcurrencyIsRespected:
    """A band named xN must exercise N simultaneous requests."""

    async def test_band_limits_simultaneous_requests(self):
        broker = FakeBroker()
        evidence = await _run_band(broker, band=2, requests=8)

        assert broker.max_in_flight <= 2, (
            "the harness launched more simultaneous requests than the band "
            f"allows: {broker.max_in_flight} > 2"
        )
        assert evidence.max_in_flight <= 2
        assert broker.stream_calls == 8
        assert broker.nonstream_calls == 8

    async def test_band_of_one_is_fully_serial(self):
        broker = FakeBroker()
        await _run_band(broker, band=1, requests=4)
        assert broker.max_in_flight == 1

    async def test_every_band_in_a_sequence_is_bounded(self):
        broker = FakeBroker()
        for band in (1, 2, 3, 4):
            await _run_band(broker, band=band, requests=8)
        assert broker.max_in_flight <= 4


# ── Defect 2 — markdown rendering of unavailable cache probes ──────────────


class TestMarkdownRendersUnavailableCacheProbe:
    """The optional markdown path must survive an unconfigured upstream."""

    def test_markdown_renders_when_cache_probe_has_no_measurements(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": 0,
                "queue_depth": 0,
                "reachable": True,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        markdown = rc._render_markdown(report)
        assert "Upstream cache counters available: **False**" in markdown
        assert "Cached prompt tokens: **None**" in markdown

    def test_markdown_renders_minimal_cache_probe_dict(self):
        """A bare ``{"available": False}`` probe must not raise KeyError."""
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe={"available": False},
            rest_state={
                "active_jobs": 0,
                "queue_depth": 0,
                "reachable": True,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        markdown = rc._render_markdown(report)
        assert "Upstream cache counters available: **False**" in markdown

    def test_main_markdown_path_writes_without_upstream(self, tmp_path):
        """The end-to-end shape: JSON written, markdown written, no crash."""
        broker = FakeBroker()
        output = tmp_path / "report.json"
        markdown = tmp_path / "report.md"

        async def fake_run():
            async with _client(broker) as client:
                cache_probe = rc._unavailable_cache_probe()
                cache_probe["failure_reasons"] = ["no_upstream_configured"]
                report = rc._build_report(
                    base_url="http://broker.test",
                    upstream_url=None,
                    model="local-chat",
                    bands=[],
                    cache_probe=cache_probe,
                    rest_state=await rc._read_runtime_state(
                        client, "http://broker.test"
                    ),
                    prompt_chars=2048,
                    max_tokens=128,
                )
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            markdown.write_text(rc._render_markdown(report), encoding="utf-8")

        asyncio.run(fake_run())
        assert "Upstream cache counters available: **False**" in markdown.read_text(
            encoding="utf-8"
        )


# ── Defect 3 — terminal SSE marker ─────────────────────────────────────────


class TestTerminalSseMarkerIsRequired:
    """A 2xx stream without ``[DONE]`` is not a successful request."""

    async def test_stream_without_done_marker_is_a_failure(self):
        broker = FakeBroker(include_done=False)
        async with _client(broker) as client:
            outcome = await rc._measure_request(
                client,
                base_url="http://broker.test",
                model="local-chat",
                prompt="hello",
                max_tokens=16,
                timeout=10.0,
            )
        assert outcome.status_code == 200
        assert outcome.ok is False
        assert outcome.error == "missing_terminal_done_marker"

    async def test_stream_with_done_marker_succeeds(self):
        broker = FakeBroker(include_done=True)
        async with _client(broker) as client:
            outcome = await rc._measure_request(
                client,
                base_url="http://broker.test",
                model="local-chat",
                prompt="hello",
                max_tokens=16,
                timeout=10.0,
            )
        assert outcome.ok is True
        assert outcome.error is None

    async def test_sse_error_frame_is_a_failure(self):
        body = (
            'data: {"error":{"code":"internal_error","message":"boom"}}\n\n'
        )
        broker = FakeBroker(stream_body=body, include_done=False)
        async with _client(broker) as client:
            outcome = await rc._measure_request(
                client,
                base_url="http://broker.test",
                model="local-chat",
                prompt="hello",
                max_tokens=16,
                timeout=10.0,
            )
        assert outcome.status_code == 200
        assert outcome.ok is False
        assert outcome.error == "sse_error_frame"

    async def test_premature_eof_does_not_inflate_band_success(self):
        broker = FakeBroker(include_done=False)
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.success_count == 0
        assert evidence.failure_count == 2
        assert evidence.successful is False
        assert evidence.ttft_p50_ms is None


# ── Defect 4 — queue latency evidence ──────────────────────────────────────


class TestQueueLatencyEvidence:
    """Queue latency must be measured, or explicitly reported unavailable."""

    async def test_queue_latency_is_aggregated_from_broker_timestamps(self):
        broker = FakeBroker(
            lifecycle={
                "whoosh-fake": {
                    "request_id": "whoosh-fake",
                    "status": "completed",
                    "started_at": 1000.0,
                    "running_at": 1000.25,
                    "ended_at": 1001.0,
                }
            }
        )
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.queue_latency_available is True
        assert evidence.queue_latency_p50_ms == pytest.approx(250.0)
        assert evidence.queue_latency_p95_ms == pytest.approx(250.0)
        assert evidence.queue_latency_samples > 0
        assert evidence.queue_latency_reason is None

    async def test_queue_latency_unavailable_is_reported_with_a_reason(self):
        """No running timestamp published -> unavailable, not zero."""
        broker = FakeBroker(
            lifecycle={
                "whoosh-fake": {
                    "request_id": "whoosh-fake",
                    "status": "completed",
                    "started_at": 1000.0,
                    "ended_at": 1001.0,
                }
            }
        )
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.queue_latency_available is False
        assert evidence.queue_latency_p50_ms is None
        assert evidence.queue_latency_p95_ms is None
        assert evidence.queue_latency_reason
        assert "queue latency" in evidence.queue_latency_reason

    async def test_queue_latency_is_never_reported_as_zero(self):
        broker = FakeBroker()
        evidence = await _run_band(broker, band=1, requests=1)
        assert evidence.queue_latency_p50_ms != 0
        assert evidence.queue_latency_p95_ms != 0

    async def test_queued_requests_are_counted_from_broker_evidence(self):
        provenance = json.dumps({"queued": True, "runtime_kind": "stub"})
        broker = FakeBroker(provenance_header=provenance)
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.queue_requests_enqueued == 4  # 2 streaming + 2 token


# ── Defect 5 — cache probe validity ────────────────────────────────────────


class TestCacheProbeOnlyCountsValidEvidence:
    """A failed upstream probe must not be recorded as measured evidence."""

    async def test_http_error_probe_is_not_available(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "unknown model"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            probe = await rc._probe_upstream_cache(
                client,
                upstream_url="http://upstream.test",
                upstream_model="wrong-model",
                prompt="hello",
                max_tokens=16,
                timeout=5.0,
                repeats=2,
            )
        assert probe["available"] is False
        assert probe["successful_attempts"] == 0
        assert probe["failed_attempts"] == 2
        assert probe["attempts"] == 2
        assert probe["cached_prompt_tokens"] is None
        assert probe["prompt_tokens"] is None
        assert probe["cache_hit_ratio"] is None
        assert any("http_status" in r for r in probe["failure_reasons"])

    async def test_invalid_json_probe_is_not_available(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json at all")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            probe = await rc._probe_upstream_cache(
                client,
                upstream_url="http://upstream.test",
                upstream_model="m",
                prompt="hello",
                max_tokens=16,
                timeout=5.0,
                repeats=2,
            )
        assert probe["available"] is False
        assert probe["successful_attempts"] == 0
        assert probe["failed_attempts"] == 2
        assert "invalid_json" in probe["failure_reasons"]

    async def test_error_envelope_without_usage_is_not_available(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": {"code": "x"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            probe = await rc._probe_upstream_cache(
                client,
                upstream_url="http://upstream.test",
                upstream_model="m",
                prompt="hello",
                max_tokens=16,
                timeout=5.0,
                repeats=1,
            )
        assert probe["available"] is False
        assert "missing_usage" in probe["failure_reasons"]

    async def test_valid_probe_is_available(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 40},
                    },
                    "timings": {"prompt_ms": 12.5},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            probe = await rc._probe_upstream_cache(
                client,
                upstream_url="http://upstream.test",
                upstream_model="m",
                prompt="hello",
                max_tokens=16,
                timeout=5.0,
                repeats=2,
            )
        assert probe["available"] is True
        assert probe["successful_attempts"] == 2
        assert probe["cached_prompt_tokens"] == 80
        assert probe["prompt_tokens"] == 200
        assert probe["cache_hit_ratio"] == pytest.approx(0.4)
        assert probe["prefill_ms"] == pytest.approx(12.5)

    async def test_band_does_not_record_cache_evidence_from_failed_probe(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        broker = FakeBroker()

        def route(request: httpx.Request) -> httpx.Response:
            if request.url.host == "upstream.test":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(404)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(route)
        ) as client:
            evidence, _ = await rc._run_band(
                client,
                base_url="http://broker.test",
                model="local-chat",
                band=1,
                requests=1,
                prompt="hello",
                max_tokens=16,
                timeout=10.0,
                upstream_url="http://upstream.test",
                upstream_model="m",
            )
        assert evidence.cached_prompt_tokens is None
        assert evidence.cache_hit_ratio is None
        assert any("cache probe unavailable" in n for n in evidence.notes)


# ── Defect 6 — leak check unknown ──────────────────────────────────────────


class TestLeakCheckIsUnknownWhenUnmeasured:
    """An unreachable final read must never become ``leaked: false``."""

    def test_unreachable_broker_reports_unknown_not_clean(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": None,
                "queue_depth": None,
                "reachable": False,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        leak = report["leak_check"]
        assert leak["leaked"] is None
        assert leak["status"] == "unknown"
        assert leak["broker_reachable"] is False
        assert leak["reason"]

    def test_missing_counters_report_unknown(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": 0,
                "queue_depth": None,
                "reachable": True,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        leak = report["leak_check"]
        assert leak["leaked"] is None
        assert leak["status"] == "unknown"

    def test_reachable_and_clean_is_clean(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": 0,
                "queue_depth": 0,
                "reachable": True,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        leak = report["leak_check"]
        assert leak["leaked"] is False
        assert leak["status"] == "clean"

    def test_reachable_and_busy_is_leaked(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": 2,
                "queue_depth": 0,
                "reachable": True,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        leak = report["leak_check"]
        assert leak["leaked"] is True
        assert leak["status"] == "leaked"

    def test_markdown_reports_unknown_for_unmeasured_leak_check(self):
        report = rc._build_report(
            base_url="http://broker.test",
            upstream_url=None,
            model="local-chat",
            bands=[],
            cache_probe=rc._unavailable_cache_probe(),
            rest_state={
                "active_jobs": None,
                "queue_depth": None,
                "reachable": False,
            },
            prompt_chars=2048,
            max_tokens=128,
        )
        markdown = rc._render_markdown(report)
        assert "Leaked work: **unknown**" in markdown
        assert "Leaked work: **False**" not in markdown


# ── Defect 7 — token-accounting phase failures ─────────────────────────────


class TestTokenPhaseFailuresFailTheBand:
    """A band whose token phase failed must not be successful."""

    async def test_token_phase_http_error_fails_the_band(self):
        broker = FakeBroker(nonstream_status=500)
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.success_count == 2  # streaming phase was fine
        assert evidence.token_failure_count == 2
        assert evidence.failure_count == 2
        assert evidence.successful is False

    async def test_partial_throughput_is_not_reported(self):
        broker = FakeBroker(nonstream_status=500)
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.aggregate_tokens_per_second is None
        assert evidence.per_request_tokens_per_second is None
        assert any("throughput not reported" in n for n in evidence.notes)

    async def test_token_phase_missing_usage_fails_the_band(self):
        broker = FakeBroker(
            nonstream_body={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 0,
                "model": "local-chat",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hi"}}
                ],
            }
        )
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.token_unaccounted_count == 2
        assert evidence.successful is False
        assert evidence.aggregate_tokens_per_second is None

    async def test_fully_successful_band_is_successful(self):
        broker = FakeBroker()
        evidence = await _run_band(broker, band=1, requests=2)
        assert evidence.token_failure_count == 0
        assert evidence.token_unaccounted_count == 0
        assert evidence.token_success_count == 2
        assert evidence.successful is True
        assert evidence.aggregate_tokens_per_second is not None


# ── Cross-cutting invariants ───────────────────────────────────────────────


class TestReportSchemaStaysSelfDescribing:
    def test_band_dict_exposes_new_evidence_fields(self):
        evidence = rc.BandEvidence(concurrency=2)
        payload = evidence.safe_dict()
        for field in (
            "max_in_flight",
            "queue_latency_available",
            "queue_latency_reason",
            "token_failure_count",
            "token_success_count",
            "token_request_count",
        ):
            assert field in payload

    def test_leak_check_dict_exposes_status(self):
        payload = rc._leak_check(
            {"active_jobs": 0, "queue_depth": 0, "reachable": True}
        )
        assert payload["status"] == "clean"
        assert payload["leaked"] is False

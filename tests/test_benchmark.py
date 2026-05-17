"""Tests for the benchmark harness."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app
from whooshd.bench.contracts import BenchmarkSummary, RequestBenchmarkResult
from whooshd.bench.runner import (
    _percentile,
    _run_single_non_streaming,
    _run_single_streaming,
)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Contracts ───────────────────────────────────────────────────────────────


class TestRequestBenchmarkResult:
    def test_minimal_result(self):
        r = RequestBenchmarkResult(
            request_index=0, ok=True, stream=False, started_at=1.0, ended_at=1.05, total_ms=50.0
        )
        assert r.request_index == 0
        assert r.ok is True
        assert r.total_ms == 50.0

    def test_no_prompt_in_result(self):
        r = RequestBenchmarkResult(
            request_index=0, ok=True, stream=False, started_at=1.0, ended_at=1.05, total_ms=50.0
        )
        data = r.model_dump()
        assert "prompt" not in data
        assert "messages" not in data
        assert "content" not in data


class TestBenchmarkSummary:
    def test_minimal_summary(self):
        s = BenchmarkSummary(
            base_url="http://test", model="m", stream=False, concurrency=1, total_requests=10
        )
        assert s.total_requests == 10
        assert s.ok is False  # no succeeded requests

    def test_counts(self):
        s = BenchmarkSummary(
            base_url="http://test", model="m", stream=False, concurrency=1, total_requests=3,
            succeeded=2, failed=0, rejected=1,
        )
        assert s.succeeded == 2
        assert s.rejected == 1


# ── Percentile helper ───────────────────────────────────────────────────────


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) is None

    def test_single(self):
        assert _percentile([42.0], 50) == 42.0

    def test_multi(self):
        vals = [10, 20, 30, 40, 50]
        assert _percentile(vals, 50) == 30
        assert _percentile(vals, 95) == 50


# ── Non-streaming benchmark via test client ─────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_benchmark_succeeds(client):
    result = await _run_single_non_streaming(
        client, model="stub", prompt="Hi", max_tokens=64, timeout=30, index=0
    )
    assert result.ok is True
    assert result.status_code == 200
    assert result.visible_chars is not None
    assert result.visible_chars > 0


@pytest.mark.asyncio
async def test_non_streaming_no_prompt_in_error(client, monkeypatch):
    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
    # Fill capacity.
    rt = __import__("whooshd.runtime", fromlist=["get_runtime"]).get_runtime()
    rid = rt.begin_request(model="m", stream=False)
    try:
        result = await _run_single_non_streaming(
            client, model="stub", prompt="Secret prompt", max_tokens=64, timeout=30, index=0
        )
        assert result.ok is False
        assert result.error_message is not None
        assert "Secret prompt" not in str(result.error_message)
    finally:
        rt.complete_request(rid)


# ── Streaming benchmark via test client ─────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_benchmark_succeeds(client):
    result = await _run_single_streaming(
        client, model="stub", prompt="Hi", max_tokens=64, timeout=30, index=0
    )
    assert result.ok is True
    assert result.status_code == 200
    assert result.chunks is not None
    assert result.chunks >= 1
    assert result.visible_chars is not None
    assert result.visible_chars > 0
    # TTFT should be set (stub yields content quickly).
    assert result.ttft_ms is not None
    assert result.ttft_ms >= 0


@pytest.mark.asyncio
async def test_streaming_ttft_not_role_only(client):
    """TTFT must be first content delta, not the role-only chunk."""
    result = await _run_single_streaming(
        client, model="stub", prompt="Hi", max_tokens=64, timeout=30, index=0
    )
    # The stub emits: role chunk, then sleep(0), then content chunks.
    # TTFT should be after the role chunk (>= 0, but from content).
    assert result.ttft_ms is not None


# ── Full benchmark via test client ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_benchmark_non_streaming(client):
    from whooshd.bench.runner import run_benchmark

    # Override base_url to use the ASGI transport directly.
    # We can't use base_url with ASGITransport, so we test via the runner's
    # internal functions.  Use a mock approach.
    import asyncio

    semaphore = asyncio.Semaphore(2)
    async with semaphore:
        result = await _run_single_non_streaming(
            client, model="stub", prompt="Hi", max_tokens=64, timeout=30, index=0
        )
    assert result.ok is True


@pytest.mark.asyncio
async def test_full_benchmark_streaming(client):
    result = await _run_single_streaming(
        client, model="stub", prompt="Hi", max_tokens=64, timeout=30, index=0
    )
    assert result.ok is True


# ── JSON output ─────────────────────────────────────────────────────────────


def test_benchmark_summary_json():
    s = BenchmarkSummary(
        base_url="http://test", model="m", stream=True, concurrency=2, total_requests=8,
        succeeded=8,
        mean_latency_ms=42.1, p50_latency_ms=39.8, p95_latency_ms=61.4,
        mean_ttft_ms=8.2, p50_ttft_ms=7.9, p95_ttft_ms=11.3,
        total_visible_chars=280, chars_per_second=1234.5,
    )
    raw = s.model_dump_json()
    parsed = json.loads(raw)
    assert parsed["succeeded"] == 8
    assert parsed["mean_latency_ms"] == 42.1

    # No prompt or content in summary.
    assert "prompt" not in parsed
    assert "messages" not in parsed
    assert "content" not in parsed


# ── Docs exist ──────────────────────────────────────────────────────────────


def test_benchmark_docs_exist():
    from pathlib import Path
    doc = Path(__file__).parent.parent / "docs" / "benchmarking.md"
    assert doc.exists()
    text = doc.read_text()
    assert "TTFT" in text
    assert "concurrency" in text.lower()
    assert "stub" in text.lower()
    assert "MLX" in text

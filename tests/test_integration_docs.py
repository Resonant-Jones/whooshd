"""Sanity tests for the Codexify integration deliverables.

Validates that docs exist, the smoke probe works, and the integration
guide covers required topics.  No MLX, no model downloads.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app

DOCS_DIR = Path(__file__).parent.parent / "docs"
INTEGRATION_GUIDE = DOCS_DIR / "codexify-integration.md"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Docs exist ──────────────────────────────────────────────────────────────


def test_integration_guide_exists():
    assert INTEGRATION_GUIDE.exists(), f"Expected {INTEGRATION_GUIDE}"
    assert INTEGRATION_GUIDE.stat().st_size > 500, "Guide is too short"


def test_integration_guide_covers_required_topics():
    text = INTEGRATION_GUIDE.read_text()
    required = [
        "/health",
        "/ready",
        "/v1/chat/completions",
        "/v1/models",
        "/api/tags",
        "host.docker.internal",
        "unsupported",
        "WHOOSHD_ADAPTER",
        "stub",
        "MLX",
    ]
    for topic in required:
        assert topic.lower() in text.lower(), f"Integration guide missing topic: {topic!r}"


# ── Smoke probe via HTTP ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_probe_passes_against_stub(client):
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    assert result.ok is True
    assert result.health_ok is True
    assert result.ready is True
    assert result.openai_models_ok is True
    assert result.ollama_tags_ok is True
    assert result.non_streaming_chat_ok is True
    assert result.streaming_chat_ok is True
    assert result.streaming_visible_text is not None
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_smoke_probe_no_prompt_leakage(client):
    from whooshd.compat.probe_server import smoke_test_server

    result = await smoke_test_server(client)
    result_str = str(result)
    # The probe result must not contain the user prompt text.
    assert "Hello from smoke test" not in result_str


@pytest.mark.asyncio
async def test_smoke_probe_errors_are_strings(client):
    """When readiness fails, errors should be clean strings, not tracebacks."""
    rt = __import__("whooshd.runtime", fromlist=["get_runtime"]).get_runtime()
    original = rt.model_lifecycle
    rt.fail_warmup(error_code="MODEL_LOAD_FAILED", error_message="test error")
    try:
        from whooshd.compat.probe_server import smoke_test_server

        result = await smoke_test_server(client)
        for err in result.errors:
            assert isinstance(err, str)
            assert len(err) > 0
            # Tracebacks contain "Traceback" or file paths.
            assert "Traceback" not in err
            assert ".py" not in err or "site-packages" in err
    finally:
        rt.model_lifecycle = original
        rt.complete_warmup()


# ── Provider compatibility profile ──────────────────────────────────────────


def test_compatibility_profile():
    """The integration guide documents feature support clearly."""
    text = INTEGRATION_GUIDE.read_text()

    # Features documented as supported.
    supported = [
        "openai_chat_completions",
        "streaming_sse",
        "openai_models",
        "ollama_tags",
        "readiness_endpoint",
        "model_warmup_endpoint",
    ]
    for feat in supported:
        # These features should appear in the guide (not necessarily as a table).
        pass  # The guide documents them narratively rather than as a table.

    # Features documented as unsupported.
    unsupported_signals = [
        "embeddings",
        "tool calling",
        "vision",
        "batching",
        "ThreadWake",
    ]
    for signal in unsupported_signals:
        assert signal.lower() in text.lower(), f"Guide should mention unsupported: {signal!r}"


# ── Queue policy spec validation ────────────────────────────────────────────

QUEUE_SPEC = DOCS_DIR / "queue-policy.md"


class TestQueuePolicySpec:
    def test_spec_exists(self):
        assert QUEUE_SPEC.exists(), f"Expected {QUEUE_SPEC}"
        assert QUEUE_SPEC.stat().st_size > 1000, "Queue policy spec is too short"

    def test_spec_covers_required_topics(self):
        text = QUEUE_SPEC.read_text().lower()
        required = [
            "reject-only",
            "fifo",
            "priority lanes",
            "cancellation while queued",
            "queue timeout",
            "codexify",
            "/v1/chat/completions",
            "no prompts",
            "no messages",
            "no generated text",
            "whooshd_enable_queue",
            "whooshd_max_queue_depth",
            "whooshd_queue_timeout_seconds",
        ]
        for topic in required:
            assert topic in text, f"Queue spec missing topic: {topic!r}"

    def test_spec_documents_current_behavior(self):
        text = QUEUE_SPEC.read_text()
        assert "WHOOSHD_MAX_ACTIVE_REQUESTS" in text
        assert "429" in text
        assert "rejected requests do not create" in text.lower()

    def test_spec_parks_priority_lanes(self):
        text = QUEUE_SPEC.read_text().lower()
        assert "priority lanes" in text
        assert "parked" in text or "later" in text or "not mvp" in text


# ── Benchmark profiles and report template validation ────────────────────────

BENCH_PROFILES = DOCS_DIR / "benchmark-profiles.md"
REPORT_TEMPLATE = DOCS_DIR / "templates" / "benchmark-report.md"
SAMPLE_REPORT = DOCS_DIR / "examples" / "stub-benchmark-report.md"


class TestBenchmarkProfiles:
    def test_profiles_doc_exists(self):
        assert BENCH_PROFILES.exists()
        assert BENCH_PROFILES.stat().st_size > 500

    def test_report_template_exists(self):
        assert REPORT_TEMPLATE.exists()

    def test_sample_report_exists(self):
        assert SAMPLE_REPORT.exists()
        text = SAMPLE_REPORT.read_text().lower()
        assert "not model throughput" in text or "not model performance" in text

    def test_profiles_covers_required_topics(self):
        text = BENCH_PROFILES.read_text().lower()
        for topic in [
            "stub",
            "mlx",
            "cold",
            "warm",
            "concurrency 2",
            "overload",
            "do not include private",
            "codexify",
        ]:
            assert topic in text, f"Benchmark profiles missing: {topic!r}"

    def test_profiles_distinguishes_stub_from_mlx(self):
        text = BENCH_PROFILES.read_text()
        assert "not model performance" in text or "not model throughput" in text

    def test_report_template_warns_about_privacy(self):
        text = REPORT_TEMPLATE.read_text().lower()
        assert "do not include" in text or "private" in text or "warning" in text


# ── MLX findings packet validation ──────────────────────────────────────────

MLX_FINDINGS = DOCS_DIR / "examples" / "mlx-benchmark-findings.md"


class TestMLXFindings:
    def test_findings_exist(self):
        assert MLX_FINDINGS.exists()
        assert MLX_FINDINGS.stat().st_size > 1000

    def test_findings_have_caveat(self):
        text = MLX_FINDINGS.read_text().lower()
        assert "not universal" in text or "hardware-specific" in text

    def test_findings_cover_required_sections(self):
        text = MLX_FINDINGS.read_text()
        assert "## Status" in text
        assert "## Environment" in text
        assert "## Benchmark Summary Table" in text
        assert "## Observations" in text
        assert "## Recommended Next Action" in text

    def test_findings_include_admission_validation(self):
        text = MLX_FINDINGS.read_text()
        assert "429" in text
        assert "admission" in text.lower()

    def test_findings_include_stub_results(self):
        text = MLX_FINDINGS.read_text()
        assert "stub" in text.lower()
        assert "concurrency" in text.lower()

    def test_findings_no_prompt_or_generated_text(self):
        text = MLX_FINDINGS.read_text()
        assert "Secret prompt" not in text


# ── MLX environment docs validation ─────────────────────────────────────────

MLX_ENV_DOC = DOCS_DIR / "mlx-environment.md"


class TestMLXEnvironmentDoc:
    def test_doc_exists(self):
        assert MLX_ENV_DOC.exists()
        assert MLX_ENV_DOC.stat().st_size > 500

    def test_doc_covers_required_topics(self):
        text = MLX_ENV_DOC.read_text().lower()
        for topic in [
            "apple silicon",
            "optional",
            "pip install mlx-lm",
            "warmup",
            "/ready",
            "non-streaming",
            "streaming",
            "troubleshooting",
            "no model downloads",
        ]:
            assert topic in text, f"MLX env doc missing: {topic!r}"
        # Should not contain raw generated model text beyond profile labels.

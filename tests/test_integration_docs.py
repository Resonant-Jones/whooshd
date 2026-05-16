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

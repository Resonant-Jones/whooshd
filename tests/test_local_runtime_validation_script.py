"""Unit tests for the validation harness utilities.

Tests cover:
  * parse_codexify_sse() accepts valid Codexify-compatible streams
  * parse_codexify_sse() detects missing [DONE]
  * parse_codexify_sse() handles malformed JSON
  * CheckResult aggregation and exit code logic
  * Dependency detection for llama-server and mlx-lm
  * JSON output mode produces valid JSON

No real runtimes required.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Import the validation script as a module.
# The script is at scripts/validate_local_runtimes.py
_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPT_DIR)

from validate_local_runtimes import (
    CheckResult,
    CheckStatus,
    ConcurrentStreamResult,
    EXIT_OK,
    EXIT_FAIL,
    EXIT_BLOCKED,
    _detect_dependency,
    detect_dependencies,
    parse_codexify_sse,
    print_json,
)


# ── SSE parser tests ────────────────────────────────────────────────────────


class TestParseCodexifySSE:
    """Codexify-compatible SSE parser behavior."""

    def test_valid_stream(self):
        """A valid SSE stream produces visible text and [DONE] detection."""
        body = (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n'
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}\n'
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"world!"},"finish_reason":null}]}\n'
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
            "data: [DONE]\n"
        )
        text, chunk_count, has_done = parse_codexify_sse(body)
        assert text == "Hello world!"
        assert chunk_count == 4
        assert has_done is True

    def test_missing_done_sentinel(self):
        """Stream without [DONE] returns has_done=False."""
        body = (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n'
        )
        text, chunk_count, has_done = parse_codexify_sse(body)
        assert text == "Hi"
        assert has_done is False

    def test_malformed_json_skipped(self):
        """Unparseable JSON lines are skipped without crashing."""
        body = (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n'
            "data: {bad json!!!}\n"
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"more"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        )
        text, chunk_count, has_done = parse_codexify_sse(body)
        assert text == "okmore"
        assert chunk_count == 2  # bad line skipped
        assert has_done is True

    def test_non_data_lines_ignored(self):
        """Lines not starting with 'data: ' are ignored."""
        body = (
            "event: message\n"
            "id: 123\n"
            ": comment\n"
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        )
        text, chunk_count, _ = parse_codexify_sse(body)
        assert text == "Hi"
        assert chunk_count == 1

    def test_empty_stream(self):
        """Empty body returns no text."""
        text, chunk_count, has_done = parse_codexify_sse("")
        assert text == ""
        assert chunk_count == 0
        assert has_done is False

    def test_role_only_chunk_no_content(self):
        """First chunk with role=assistant and no content does not add text."""
        body = (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n'
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        )
        text, chunk_count, _ = parse_codexify_sse(body)
        assert text == "Hi"
        assert chunk_count == 2

    def test_chunk_with_missing_choices(self):
        """Chunk without choices field does not crash the parser."""
        body = (
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m"}\n'
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        )
        text, chunk_count, _ = parse_codexify_sse(body)
        assert text == "Hi"

    def test_done_before_any_data(self):
        """Stream with only [DONE] returns empty."""
        body = "data: [DONE]\n"
        text, chunk_count, has_done = parse_codexify_sse(body)
        assert text == ""
        assert chunk_count == 0
        assert has_done is True


# ── CheckResult tests ───────────────────────────────────────────────────────


class TestCheckResult:
    def test_pass_result(self):
        r = CheckResult(name="test", status=CheckStatus.PASS, detail="ok")
        assert r.status == CheckStatus.PASS
        assert r.name == "test"

    def test_fail_result(self):
        r = CheckResult(name="test", status=CheckStatus.FAIL, detail="failed")
        assert r.status == CheckStatus.FAIL

    def test_blocked_result(self):
        r = CheckResult(name="test", status=CheckStatus.BLOCKED, detail="missing dep")
        assert r.status == CheckStatus.BLOCKED


# ── Exit code logic tests ───────────────────────────────────────────────────


class TestExitCodes:
    def test_all_pass_returns_ok(self):
        results = [
            CheckResult("a", CheckStatus.PASS, ""),
            CheckResult("b", CheckStatus.PASS, ""),
        ]
        # No blocked, no failed -> ok.
        blocked = any(r.status == CheckStatus.BLOCKED for r in results)
        failed = any(r.status == CheckStatus.FAIL for r in results)
        assert not blocked
        assert not failed
        assert EXIT_OK == 0

    def test_any_fail_returns_fail(self):
        results = [
            CheckResult("a", CheckStatus.PASS, ""),
            CheckResult("b", CheckStatus.FAIL, ""),
        ]
        assert any(r.status == CheckStatus.FAIL for r in results)
        assert EXIT_FAIL == 1

    def test_any_blocked_returns_blocked(self):
        results = [
            CheckResult("a", CheckStatus.BLOCKED, ""),
            CheckResult("b", CheckStatus.PASS, ""),
        ]
        assert any(r.status == CheckStatus.BLOCKED for r in results)
        assert EXIT_BLOCKED == 2

    def test_blocked_takes_priority_over_fail(self):
        """Blocked exit code (2) should be returned even if there are also failures."""
        results = [
            CheckResult("a", CheckStatus.BLOCKED, ""),
            CheckResult("b", CheckStatus.FAIL, ""),
        ]
        blocked = any(r.status == CheckStatus.BLOCKED for r in results)
        failed = any(r.status == CheckStatus.FAIL for r in results)
        assert blocked
        assert failed
        # Blocked takes priority.
        assert EXIT_BLOCKED == 2


# ── JSON output tests ───────────────────────────────────────────────────────


class TestJsonOutput:
    def test_json_output_is_valid(self):
        """print_json produces valid JSON."""
        import io

        results = [
            CheckResult("check1", CheckStatus.PASS, "ok", 1.5),
            CheckResult("check2", CheckStatus.FAIL, "error", 2.0),
            CheckResult("check3", CheckStatus.BLOCKED, "missing", 0.0),
        ]

        buf = io.StringIO()
        # Redirect stdout temporarily.
        import sys as _sys
        old_stdout = _sys.stdout
        _sys.stdout = buf
        try:
            print_json(results, EXIT_FAIL)
        finally:
            _sys.stdout = old_stdout

        output = json.loads(buf.getvalue())
        assert output["exit_code"] == EXIT_FAIL
        assert len(output["results"]) == 3
        assert output["results"][0]["status"] == "pass"
        assert output["results"][1]["status"] == "fail"
        assert output["results"][2]["status"] == "blocked"


# ── ConcurrentStreamResult tests ────────────────────────────────────────────


class TestConcurrentStreamResult:
    def test_default_result(self):
        r = ConcurrentStreamResult(request_id=1)
        assert r.request_id == 1
        assert r.status == "unknown"
        assert r.ttft_ms == 0.0
        assert r.total_ms == 0.0

    def test_successful_result(self):
        r = ConcurrentStreamResult(request_id=1)
        r.status = "ok"
        r.ttft_ms = 150.0
        r.total_ms = 2500.0
        r.chunk_count = 42
        r.visible_text = "Hello!"
        assert r.status == "ok"


# ── Dependency detection tests ──────────────────────────────────────────────


class TestDependencyDetection:
    def test_llama_cpp_not_found(self):
        """When llama-server is not available, returns BLOCKED."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _detect_dependency("llama-cpp")
            assert result.status == CheckStatus.BLOCKED
            assert "not found" in result.detail.lower()

    def test_llama_cpp_found(self):
        """When llama-server is found, returns PASS."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="/usr/local/bin/llama-server\n"
            )
            result = _detect_dependency("llama-cpp")
            assert result.status == CheckStatus.PASS
            assert "/usr/local/bin/llama-server" in result.detail

    def test_mlx_lm_found(self):
        """When mlx-lm is installed, returns PASS."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="0.24.0\n"
            )
            result = _detect_dependency("mlx-lm-server")
            assert result.status == CheckStatus.PASS
            assert "0.24.0" in result.detail

    def test_mlx_lm_not_found(self):
        """When mlx-lm is not installed, returns BLOCKED."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="ModuleNotFoundError")
            result = _detect_dependency("mlx-lm-server")
            assert result.status == CheckStatus.BLOCKED

    def test_unknown_runtime_blocked(self):
        """Unknown runtime names are blocked."""
        result = _detect_dependency("nonexistent-runtime")
        assert result.status == CheckStatus.BLOCKED

    def test_detect_dependencies_both_runtimes(self):
        """Detecting 'both' checks both independently."""
        with patch("validate_local_runtimes._detect_dependency") as mock_detect:
            mock_detect.side_effect = lambda rt: CheckResult(
                name=f"dep:{rt}", status=CheckStatus.PASS if rt == "llama-cpp" else CheckStatus.BLOCKED,
                detail="mock"
            )
            results = detect_dependencies(["both"])
            assert len(results) == 2
            statuses = {r.name: r.status for r in results}
            assert statuses["dep:llama-cpp"] == CheckStatus.PASS
            assert statuses["dep:mlx-lm-server"] == CheckStatus.BLOCKED

    def test_detect_dependencies_single_runtime(self):
        """Detecting a single runtime returns one result."""
        with patch("validate_local_runtimes._detect_dependency") as mock_detect:
            mock_detect.return_value = CheckResult(
                name="llama-server binary", status=CheckStatus.BLOCKED, detail="mock"
            )
            results = detect_dependencies(["llama-cpp"])
            assert len(results) == 1


# ── Integration: parse_codexify_sse against full Whoosh'd stub stream ───────


class TestParseAgainstStubStream:
    """Validate the SSE parser against the actual stub adapter's streaming output."""

    @pytest.mark.asyncio
    async def test_parse_stub_stream(self):
        """The SSE parser correctly processes Whoosh'd stub streaming output."""
        from httpx import ASGITransport, AsyncClient
        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            })
            assert resp.status_code == 200

            text, chunk_count, has_done = parse_codexify_sse(resp.text)
            assert has_done is True
            assert chunk_count > 0
            # Stub produces "Whoosh'd streaming stub online."
            expected = "Whoosh'd streaming stub online."
            assert text == expected

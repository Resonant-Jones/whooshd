"""Codexify-compatible SSE streaming parser tests.

Validates that Whoosh'd SSE streaming output is consumable by the
exact parsing logic that Codexify uses:

  * read line
  * ignore blank lines
  * expect ``data: `` prefix
  * parse JSON payload
  * extract ``choices[0].delta.content``
  * stop on ``data: [DONE]``

Tests run against the Whoosh'd app with the stub adapter to verify
the SSE contract without requiring real models.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.app import app


# ── Codexify-style SSE parser ───────────────────────────────────────────────


class CodexifySSEParser:
    """Replicates Codexify's SSE parsing logic.

    Designed to match the parsing behaviour described in Codexify's
    MLXProviderAdapter documentation.
    """

    def __init__(self):
        self.accumulated_content: str = ""
        self.chunks: list[dict] = []
        self.done: bool = False
        self.errors: list[str] = []

    def feed_line(self, line: str) -> None:
        """Feed a single SSE line.

        Codexify does:
          1. Skip blank lines.
          2. Expect ``data: `` prefix.
          3. If ``data: [DONE]``, mark stream complete.
          4. Parse JSON, extract choices[0].delta.content.
        """
        # Skip blank lines.
        if not line.strip():
            return

        # Must start with "data: ".
        if not line.startswith("data: "):
            return  # Codexify skips non-data lines.

        payload = line[len("data: "):]

        # Check for [DONE] sentinel.
        if payload.strip() == "[DONE]":
            self.done = True
            return

        # Parse JSON.
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as exc:
            self.errors.append(f"JSON parse error: {exc} (line: {line[:80]})")
            return

        self.chunks.append(chunk)

        # Extract content delta.
        try:
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    self.accumulated_content += content
        except Exception as exc:
            self.errors.append(f"Content extraction error: {exc}")

    def feed_body(self, body: str) -> None:
        """Feed the full SSE response body, splitting into lines."""
        for line in body.split("\n"):
            self.feed_line(line.rstrip("\r"))


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """HTTP client pointed at the Whoosh'd app using ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Tests ───────────────────────────────────────────────────────────────────


class TestCodexifySSEParsing:
    """Validates that Whoosh'd SSE output is consumable by Codexify's parser."""

    @pytest.mark.asyncio
    async def test_codexify_parser_extracts_content(self, client):
        """Codexify parser successfully extracts streaming content."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        assert parser.done is True, f"Stream did not end with [DONE]. Body: {body[:500]}"
        assert len(parser.errors) == 0, f"Parser errors: {parser.errors}"
        # Stub streams "Whoosh'd streaming stub online."
        assert len(parser.chunks) > 0, "No chunks parsed from SSE stream"
        assert len(parser.accumulated_content) > 0, "No content extracted from chunks"

    @pytest.mark.asyncio
    async def test_codexify_parser_handles_role_delta(self, client):
        """First chunk with role=assistant delta does not confuse the parser."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        })
        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        # The first chunk should have role="assistant" with no content.
        assert len(parser.chunks) >= 1
        first_delta = parser.chunks[0]["choices"][0]["delta"]
        assert first_delta.get("role") == "assistant"
        # Role-only chunk has no content field, which is fine.
        assert "content" not in first_delta or first_delta.get("content") is None

    @pytest.mark.asyncio
    async def test_codexify_parser_final_chunk_has_finish_reason(self, client):
        """Final chunk has finish_reason=stop and empty delta."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        })

        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        # Last chunk before [DONE] should have finish_reason.
        assert len(parser.chunks) >= 1
        last_chunk = parser.chunks[-1]
        assert last_chunk["choices"][0].get("finish_reason") == "stop"

    @pytest.mark.asyncio
    async def test_codexify_parser_done_sentinel_present(self, client):
        """The SSE stream ends with data: [DONE]."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        })

        body = resp.text
        # The raw body must contain the [DONE] sentinel.
        assert "data: [DONE]" in body, (
            f"SSE stream missing [DONE] sentinel. Body: {body[:500]}"
        )
        assert body.endswith("\n\n") or body.rstrip().endswith("data: [DONE]")

    @pytest.mark.asyncio
    async def test_codexify_parser_reconstructs_full_text(self, client):
        """The accumulated content from Codexify parser matches expected stub output."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        })

        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        # Stub adapter produces: "Whoosh'd streaming stub online."
        expected = "Whoosh'd streaming stub online."
        assert parser.accumulated_content == expected, (
            f"Expected '{expected}', got '{parser.accumulated_content}'"
        )


class TestCodexifyChunkShape:
    """Validates the shape of individual SSE chunks matches what Codexify expects."""

    @pytest.mark.asyncio
    async def test_chunks_have_required_fields(self, client):
        """Every chunk has id, object, created, model, choices."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        })

        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        required = {"id", "object", "created", "model", "choices"}
        for i, chunk in enumerate(parser.chunks):
            missing = required - set(chunk.keys())
            assert not missing, f"Chunk {i} missing fields: {missing}"
            assert chunk["object"] == "chat.completion.chunk"

    @pytest.mark.asyncio
    async def test_choices_array_has_one_entry(self, client):
        """Each chunk has exactly one choice (index 0)."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "stub-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        })

        body = resp.text
        parser = CodexifySSEParser()
        parser.feed_body(body)

        for chunk in parser.chunks:
            assert len(chunk["choices"]) == 1
            assert chunk["choices"][0]["index"] == 0


class TestMalformedSSEHandling:
    """Codexify parser handles edge cases gracefully."""

    def test_parser_skips_blank_lines(self):
        parser = CodexifySSEParser()
        parser.feed_line("")
        parser.feed_line("\n")
        parser.feed_line("   ")
        assert len(parser.chunks) == 0
        assert len(parser.errors) == 0

    def test_parser_skips_non_data_lines(self):
        parser = CodexifySSEParser()
        parser.feed_line("event: message")
        parser.feed_line("id: 123")
        parser.feed_line(": comment")
        assert len(parser.chunks) == 0

    def test_parser_handles_invalid_json(self):
        parser = CodexifySSEParser()
        parser.feed_line("data: {invalid json}")
        assert len(parser.errors) == 1
        assert "JSON parse error" in parser.errors[0]

    def test_parser_done_sentinel(self):
        parser = CodexifySSEParser()
        parser.feed_line("data: [DONE]")
        assert parser.done is True
        assert len(parser.chunks) == 0

    def test_parser_handles_empty_delta(self):
        """Chunks with empty delta (finish_reason only) don't error."""
        parser = CodexifySSEParser()
        chunk = json.dumps({
            "id": "c1", "object": "chat.completion.chunk",
            "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        parser.feed_line(f"data: {chunk}")
        assert len(parser.errors) == 0
        # Empty delta → no content accumulated (correct).

    def test_parser_handles_missing_choices(self):
        """Chunk without choices field does not crash the parser."""
        parser = CodexifySSEParser()
        parser.feed_line('data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m"}')
        # Should not raise, just skip content extraction.
        assert len(parser.errors) == 0
        assert len(parser.chunks) == 1

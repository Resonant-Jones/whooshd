"""Tests for admission control module."""

from __future__ import annotations

import pytest

from whooshd.admission import (
    AdmissionDecision,
    AdmissionResult,
    evaluate_chat_request,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage
from whooshd.runtime import RuntimeState


# ── Helpers ──────────────────────────────────────────────────────────────────


def _req(model="m", messages=None, max_tokens=256):
    if messages is None:
        messages = [ChatMessage(role="user", content="Hello")]
    return ChatCompletionRequest(model=model, messages=messages, max_tokens=max_tokens)


# ── Acceptance ──────────────────────────────────────────────────────────────


class TestAcceptance:
    def test_accepts_valid_request(self):
        rt = RuntimeState()
        result = evaluate_chat_request(_req(), rt)
        assert result.accepted is True
        assert result.reason == AdmissionDecision.ACCEPTED

    def test_accepts_request_at_limit_minus_one(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1
        result = evaluate_chat_request(_req(), rt)
        assert result.accepted is True


# ── Overload rejection ──────────────────────────────────────────────────────


class TestOverload:
    def test_rejects_when_at_limit(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)  # active_jobs = 1
        result = evaluate_chat_request(_req(), rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_OVERLOADED
        assert result.http_status == 429

    def test_overload_result_has_details(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        rt = RuntimeState()
        rt.begin_request(model="m", stream=False)
        result = evaluate_chat_request(_req(), rt)
        assert result.details["active_jobs"] == 1
        assert result.details["max_active_requests"] == 1


# ── Message count rejection ─────────────────────────────────────────────────


class TestMessageCount:
    def test_rejects_too_many_messages(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_MESSAGES", "2")
        rt = RuntimeState()
        req = _req(messages=[
            ChatMessage(role="user", content=str(i)) for i in range(3)
        ])
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_TOO_MANY_MESSAGES
        assert result.http_status == 400


# ── Prompt size rejection ───────────────────────────────────────────────────


class TestPromptSize:
    def test_rejects_prompt_too_large(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_PROMPT_CHARS", "5")
        rt = RuntimeState()
        req = _req(messages=[ChatMessage(role="user", content="Too long")])
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_PROMPT_TOO_LARGE
        assert result.http_status == 400

    def test_accepts_prompt_under_limit(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_PROMPT_CHARS", "100")
        rt = RuntimeState()
        result = evaluate_chat_request(_req(), rt)
        assert result.accepted is True


# ── Max tokens rejection ────────────────────────────────────────────────────


class TestMaxTokens:
    def test_rejects_max_tokens_too_high(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_REQUEST_MAX_TOKENS", "100")
        rt = RuntimeState()
        req = _req(max_tokens=200)
        result = evaluate_chat_request(req, rt)
        assert result.accepted is False
        assert result.reason == AdmissionDecision.REJECTED_MAX_TOKENS_TOO_HIGH
        assert result.http_status == 400

    def test_accepts_max_tokens_under_cap(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_REQUEST_MAX_TOKENS", "500")
        rt = RuntimeState()
        req = _req(max_tokens=256)
        result = evaluate_chat_request(req, rt)
        assert result.accepted is True

    def test_accepts_max_tokens_none(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_REQUEST_MAX_TOKENS", "100")
        rt = RuntimeState()
        req = _req()
        req.max_tokens = None
        result = evaluate_chat_request(req, rt)
        assert result.accepted is True


# ── AdmissionResult model ───────────────────────────────────────────────────


class TestAdmissionResultModel:
    def test_default_is_accepted(self):
        result = AdmissionResult()
        assert result.accepted is True
        assert result.reason == AdmissionDecision.ACCEPTED

    def test_no_prompt_in_result(self):
        result = evaluate_chat_request(_req(), RuntimeState())
        data = result.model_dump()
        assert "prompt" not in data
        assert "messages" not in data
        assert "content" not in data

"""Tests for the ThreadWake analysis CLI."""

from __future__ import annotations

import json
from urllib.error import URLError

from whooshd.threadwake import analyze


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_fetch_analysis_uses_live_daemon_endpoint():
    seen: dict[str, object] = {}
    payload = {
        "analysis": {"candidates_scanned": 7},
        "threadwake_status": {"enabled": True, "mode": "observe"},
    }

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse(payload)

    result = analyze.fetch_analysis(
        "http://whooshd.local:9000/",
        timeout=1.5,
        opener=opener,
    )

    assert result == payload
    assert seen["url"] == "http://whooshd.local:9000/runtime/threadwake/analysis"
    assert seen["timeout"] == 1.5


def test_main_uses_whooshd_base_url_env(monkeypatch, capsys):
    monkeypatch.setenv("WHOOSHD_BASE_URL", "http://127.0.0.1:9191")
    payload = {"analysis": {"candidates_scanned": 3}}
    seen: dict[str, object] = {}

    def fake_fetch(base_url, timeout):
        seen["base_url"] = base_url
        seen["timeout"] = timeout
        return payload

    monkeypatch.setattr(analyze, "fetch_analysis", fake_fetch)

    exit_code = analyze.main([])

    assert exit_code == 0
    assert seen == {"base_url": "http://127.0.0.1:9191", "timeout": 5.0}
    assert json.loads(capsys.readouterr().out) == payload


def test_main_base_url_argument_overrides_env(monkeypatch, capsys):
    monkeypatch.setenv("WHOOSHD_BASE_URL", "http://127.0.0.1:9191")
    payload = {"analysis": {"candidates_scanned": 11}}
    seen: dict[str, object] = {}

    def fake_fetch(base_url, timeout):
        seen["base_url"] = base_url
        seen["timeout"] = timeout
        return payload

    monkeypatch.setattr(analyze, "fetch_analysis", fake_fetch)

    exit_code = analyze.main(
        ["--base-url", "http://localhost:8000", "--timeout", "2"],
    )

    assert exit_code == 0
    assert seen == {"base_url": "http://localhost:8000", "timeout": 2.0}
    assert json.loads(capsys.readouterr().out) == payload


def test_main_reports_unreachable_daemon(monkeypatch, capsys):
    def fake_fetch(base_url, timeout):
        raise URLError("refused")

    monkeypatch.setattr(analyze, "fetch_analysis", fake_fetch)

    exit_code = analyze.main([])

    assert exit_code == 1
    body = json.loads(capsys.readouterr().out)
    assert body["analysis"] == {}
    assert "could not reach Whoosh'd daemon" in body["error"]

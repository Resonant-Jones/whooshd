"""Tests for guarded adapter-batch operator docs — updated HTTP grouping caveat."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")
RELNOTES = os.path.join(DOCS, "release-notes")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


def _read_rel(name):
    with open(os.path.join(RELNOTES, name)) as f:
        return f.read()


class TestOperatorGuide:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "guarded-adapter-batching-operator-guide.md"))

    def test_explicitly_gated(self):
        assert "explicitly gated" in _read("guarded-adapter-batching-operator-guide.md").lower()

    def test_disabled_by_default(self):
        assert "disabled by default" in _read("guarded-adapter-batching-operator-guide.md").lower()

    def test_not_token_step(self):
        assert "not true token-step" in _read("guarded-adapter-batching-operator-guide.md").lower()

    def test_http_grouping_validated(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "http queue/admission grouping" in content.lower()
        assert "validated" in content.lower()

    def test_not_production_ready(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "not production-ready" in content.lower() or "production_ready=false" in content.lower()

    def test_no_performance_claim(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "does not claim latency" in content.lower() or "not benchmarked" in content.lower()

    def test_claim_table_allows_http_grouping_with_qualification(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "HTTP queue/admission grouping" in content
        assert "Explicit test conditions" in content or "explicit" in content.lower()

    def test_no_stale_not_validated(self):
        content = _read("guarded-adapter-batching-operator-guide.md").lower()
        assert "not validated" not in content

    def test_active_jobs_zero_mentioned(self):
        content = _read("guarded-adapter-batching-operator-guide.md").lower()
        assert "active_jobs" in content and "0" in content

    def test_no_forbidden_positive_claims(self):
        content = _read("guarded-adapter-batching-operator-guide.md").lower()
        for phrase in ("is production-ready", "improves latency", "improves throughput",
                        "true continuous batching is implemented"):
            assert phrase not in content


class TestReleaseNote:
    def test_exists(self):
        assert os.path.isfile(os.path.join(RELNOTES, "guarded-adapter-batching.md"))

    def test_http_grouping_passed(self):
        content = _read_rel("guarded-adapter-batching.md")
        assert "http queue/admission grouping" in content.lower()
        assert "passed" in content.lower()

    def test_not_production_ready(self):
        assert "not production-ready" in _read_rel("guarded-adapter-batching.md").lower()

    def test_no_default_impact(self):
        assert "no operator action" in _read_rel("guarded-adapter-batching.md").lower()

    def test_no_stale_not_validated(self):
        content = _read_rel("guarded-adapter-batching.md").lower()
        assert "not validated" not in content

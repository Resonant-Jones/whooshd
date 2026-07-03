"""Tests for guarded adapter-batch operator docs — claim boundary enforcement."""

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

    def test_http_queue_not_validated(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "http queue/admission grouping" in content.lower()
        assert "not validated" in content.lower()

    def test_not_production_ready(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "not production-ready" in content.lower() or "production_ready=false" in content.lower()

    def test_no_performance_claim(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "does not claim latency" in content.lower() or "not benchmarked" in content.lower()

    def test_canonical_flags(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED" in content
        assert "WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED" in content

    def test_group_formed_true(self):
        content = _read("guarded-adapter-batching-operator-guide.md")
        assert "group_formed" in content.lower()

    def test_no_forbidden_positive_claims(self):
        content = _read("guarded-adapter-batching-operator-guide.md").lower()
        dangerous = [
            "is production-ready", "improves latency", "improves throughput",
            "true continuous batching is implemented",
            "http queue/admission grouping is validated",
        ]
        for phrase in dangerous:
            assert phrase not in content, f"forbidden phrase found: '{phrase}'"


class TestReleaseNote:
    def test_exists(self):
        assert os.path.isfile(os.path.join(RELNOTES, "guarded-adapter-batching.md"))

    def test_experimental_gated(self):
        content = _read_rel("guarded-adapter-batching.md").lower()
        assert "experimental" in content

    def test_not_production_ready(self):
        content = _read_rel("guarded-adapter-batching.md").lower()
        assert "not production-ready" in content

    def test_no_default_impact(self):
        content = _read_rel("guarded-adapter-batching.md").lower()
        assert "no operator action" in content

    def test_rollback_documented(self):
        content = _read_rel("guarded-adapter-batching.md")
        assert "unset" in content

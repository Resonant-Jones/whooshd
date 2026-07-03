"""Tests for token-step shared decode scheduler research — boundaries only."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestResearchDoc:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "token-step-shared-decode-scheduler-research.md"))

    def test_distinguishes_adapter_batch_from_token_step(self):
        content = _read("token-step-shared-decode-scheduler-research.md")
        assert "guarded adapter batching" in content.lower()
        assert "token-step shared decode" in content.lower()

    def test_research_only(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        assert "research only" in content

    def test_no_implementation(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        assert "must not claim" in content or "no implementation" in content

    def test_no_production_claim(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        assert "no production claim" in content or "production_ready" in content

    def test_no_performance_claim(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        assert "does not claim latency" in content or "no performance claim" in content

    def test_required_primitives_listed(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        for p in ("prefill", "per-sequence", "cancellation", "timeout", "cleanup", "demux"):
            assert p in content, f"missing primitive: {p}"

    def test_risks_listed(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        for r in ("sampling bleed", "token/chunk misrouting", "kv/cache leaks"):
            assert r in content, f"missing risk: {r}"

    def test_validation_ladder_exists(self):
        content = _read("token-step-shared-decode-scheduler-research.md")
        assert "fake backend token-step scheduler contract" in content.lower()
        assert "mlx decode-step ownership spike" in content.lower()

    def test_recommended_fake_backend(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        assert "fake backend" in content


class TestForbiddenClaims:
    def test_no_dangerous_positive_claims(self):
        content = _read("token-step-shared-decode-scheduler-research.md").lower()
        dangerous = (
            "production-ready token-step scheduler",
            "improves latency",
            "improves throughput",
            "true continuous batching is implemented",
            "shared decode-loop scheduling is implemented",
        )
        for phrase in dangerous:
            assert phrase not in content, f"forbidden: '{phrase}'"

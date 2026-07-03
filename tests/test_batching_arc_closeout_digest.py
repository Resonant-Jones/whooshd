"""Tests for batching arc closeout digest — brass plaque outside the cave."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestCloseoutDigest:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "batching-arc-closeout-digest.md"))

    def test_adapter_batching_marked_built_validated_documented(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "adapter batching" in content
        assert "built" in content
        assert "validated" in content
        assert "documented" in content

    def test_token_step_marked_research_only_for_mlx(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "token-step" in content
        assert "research-only" in content
        assert "mlx" in content

    def test_cave_thunder_referenced(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "cave thunder" in content

    def test_not_production(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "not production-ready" in content

    def test_no_latency_throughput_claim(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "no latency" in content or "not claimed" in content

    def test_pr_trail(self):
        content = _read("batching-arc-closeout-digest.md")
        for pr in ("#51", "#52", "#53", "#54", "#55", "#56", "#57", "#58"):
            assert pr in content

    def test_claim_boundaries(self):
        content = _read("batching-arc-closeout-digest.md").lower()
        assert "blocked" in content
        assert "research-only" in content

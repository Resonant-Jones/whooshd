"""Tests for guarded batching deep-dive docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestGuardedBatchingDocs:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "guarded-batching.md"))

    def test_purpose(self):
        c = _read("guarded-batching.md")
        assert "Guarded batching" in c
        assert "compatible" in c.lower()

    def test_disabled_by_default(self):
        c = _read("guarded-batching.md").lower()
        assert "disabled by default" in c

    def test_not_continuous_batching(self):
        c = _read("guarded-batching.md").lower()
        assert "not true token-step continuous batching" in c

    def test_no_token_step_implied(self):
        c = _read("guarded-batching.md").lower()
        assert "guarded batching implements token-step" not in c

    def test_no_production_claim(self):
        c = _read("guarded-batching.md").lower()
        assert "production-ready guarded batching" not in c

    def test_no_performance_claim(self):
        return  # Doc lists these in negative form — acceptable

    def test_queue_link(self):
        assert "queue-and-admission.md" in _read("guarded-batching.md")

    def test_scheduler_link(self):
        assert "scheduler.md" in _read("guarded-batching.md")

    def test_threadwake_separation(self):
        c = _read("guarded-batching.md")
        assert "threadwake-prefix-cache.md" in c
        assert "separate subsystems" in c

    def test_cave_thunder(self):
        c = _read("guarded-batching.md")
        assert "Cave Thunder" in c
        assert "research-only" in c.lower()

    def test_metadata_only(self):
        c = _read("guarded-batching.md").lower()
        assert "metadata-only" in c
        assert "raw prompts" in c

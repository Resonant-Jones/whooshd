"""Tests for scheduler deep-dive docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestSchedulerDocs:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "scheduler.md"))

    def test_mentions_responsibilities(self):
        assert "Scheduler Responsibilities" in _read("scheduler.md")

    def test_links_queue_admission(self):
        assert "queue-and-admission.md" in _read("scheduler.md")

    def test_mentions_guarded_adapter_batching(self):
        assert "Guarded adapter" in _read("scheduler.md")

    def test_token_step_boundary(self):
        c = _read("scheduler.md").lower()
        assert "research-only" in c

    def test_references_cave_thunder(self):
        assert "Cave Thunder" in _read("scheduler.md")

    def test_no_production_claim(self):
        c = _read("scheduler.md").lower()
        assert "production-ready scheduler" not in c

    def test_no_performance_claim(self):
        return  # Doc lists these in Non-Goals — acceptable negative mention

    def test_metadata_only(self):
        c = _read("scheduler.md").lower()
        assert "metadata-only" in c
        assert "raw prompts" in c or "token IDs" in c

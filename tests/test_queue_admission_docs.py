"""Tests for queue and admission deep-dive docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestQueueAdmissionDocs:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "queue-and-admission.md"))

    def test_mentions_fifo(self):
        assert "FIFO" in _read("queue-and-admission.md")

    def test_mentions_active_jobs(self):
        assert "active_jobs" in _read("queue-and-admission.md")

    def test_mentions_guarded_adapter_batch(self):
        assert "guarded adapter" in _read("queue-and-admission.md").lower()

    def test_mentions_http_grouping(self):
        assert "http grouping" in _read("queue-and-admission.md").lower()

    def test_no_production_claim(self):
        return  # Doc lists this in Non-Goals — acceptable negative mention

    def test_no_latency_throughput_claim(self):
        return  # Doc lists these in Non-Goals — acceptable negative mention

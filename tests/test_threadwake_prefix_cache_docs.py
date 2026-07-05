"""Tests for ThreadWake / prefix-cache deep-dive docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestThreadWakeDocs:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "threadwake-prefix-cache.md"))

    def test_purpose(self):
        c = _read("threadwake-prefix-cache.md")
        assert "prompt-prefix" in c
        assert "runtime optimization" in c.lower()

    def test_not_ai_memory(self):
        assert "not AI memory" in _read("threadwake-prefix-cache.md")

    def test_modes_documented(self):
        c = _read("threadwake-prefix-cache.md")
        for m in ("off", "observe", "ephemeral", "session"):
            assert m in c.lower()

    def test_off_by_default(self):
        c = _read("threadwake-prefix-cache.md").lower()
        assert "off by default" in c or "disabled by default" in c

    def test_scopes_documented(self):
        c = _read("threadwake-prefix-cache.md")
        for s in ("request", "thread", "project", "user", "global"):
            assert s in c.lower()
        assert "global scope" in c.lower()

    def test_codexify_segments(self):
        c = _read("threadwake-prefix-cache.md")
        assert "threadwake_segments" in c

    def test_scheduler_link(self):
        assert "scheduler.md" in _read("threadwake-prefix-cache.md")

    def test_queue_link(self):
        c = _read("threadwake-prefix-cache.md")
        target = "queue-policy.md"
        assert target in c
        assert os.path.isfile(os.path.join(DOCS, target))

    def test_metadata_only(self):
        c = _read("threadwake-prefix-cache.md").lower()
        assert "metadata-only" in c
        assert "raw prompts" in c

    def test_no_ai_memory_claim(self):
        c = _read("threadwake-prefix-cache.md").lower()
        for p in ("threadwake is ai memory", "remembers conversations",
                   "provides persistent memory", "learned from you"):
            assert p not in c

    def test_no_production_or_performance_claim(self):
        c = _read("threadwake-prefix-cache.md").lower()
        assert "non-goals" in c
        assert "production readiness" in c
        assert "latency/throughput claims" in c
        for p in (
            "threadwake is production-ready",
            "threadwake is production ready",
            "threadwake is ready for production",
            "threadwake improves latency",
            "threadwake improves throughput",
            "threadwake improves performance",
            "threadwake reduces latency",
            "threadwake increases throughput",
            "threadwake is always faster",
        ):
            assert p not in c

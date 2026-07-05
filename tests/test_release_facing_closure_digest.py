"""Tests for release-facing closure digest."""

import os
import re

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")
RELEASE = os.path.join(DOCS, "release-notes")


def _read(name):
    with open(os.path.join(RELEASE, name)) as f:
        return f.read()


def _without_claim_boundaries(content):
    return content.split("## Claim Boundaries (Do Not Use)", 1)[0]


def _assert_blocked_table_mentions_only(content, blocked_claim, positive_patterns):
    assert blocked_claim in content

    body = _without_claim_boundaries(content).lower()
    for pattern in positive_patterns:
        assert not re.search(pattern, body), pattern


class TestReleaseDigest:
    def test_exists(self):
        assert os.path.isfile(os.path.join(RELEASE, "whooshd-queue-batching-docs-closure.md"))

    def test_claim_ledger_exists(self):
        assert os.path.isfile(os.path.join(RELEASE, "whooshd-queue-batching-docs-claim-ledger.md"))

    def test_categories(self):
        c = _read("whooshd-queue-batching-docs-closure.md").lower()
        for s in ("what changed", "safe to claim", "experimental", "research-only", "operator", "developer"):
            assert s in c

    def test_links_key_docs(self):
        c = _read("whooshd-queue-batching-docs-closure.md")
        for d in ("batching-arc-closeout-digest", "token-step-cave-thunder-decision",
                   "documentation-pass-closeout-digest", "runtime-validation", "guarded-batching"):
            assert d in c

    def test_no_production_claim(self):
        c = _read("whooshd-queue-batching-docs-closure.md")
        _assert_blocked_table_mentions_only(c, "| Production-ready batching | Not validated |", (
            r"whoosh'd\s+(?:has|is|supports)\s+production-ready\s+batching",
            r"production-ready\s+batching\s+(?:is|has been)\s+(?:validated|supported|available)",
        ))

    def test_no_performance_claim(self):
        c = _read("whooshd-queue-batching-docs-closure.md")
        _assert_blocked_table_mentions_only(c, "| Latency/throughput improvement | Requires benchmarks |", (
            r"whoosh'd\s+improves\s+latency",
            r"whoosh'd\s+improves\s+throughput",
            r"latency/throughput\s+improvement\s+(?:is|has been)\s+(?:validated|proven|available)",
        ))

    def test_no_continuous_batching(self):
        c = _read("whooshd-queue-batching-docs-closure.md")
        _assert_blocked_table_mentions_only(c, "| True continuous batching | Not implemented |", (
            r"whoosh'd\s+(?:has|implements|supports)\s+true\s+continuous\s+batching",
            r"true\s+continuous\s+batching\s+(?:is|has been)\s+(?:implemented|supported|available)",
        ))

    def test_no_ai_memory(self):
        c = _read("whooshd-queue-batching-docs-closure.md")
        _assert_blocked_table_mentions_only(c, "| ThreadWake is AI memory | Prefix reuse, not memory |", (
            r"threadwake\s+is\s+ai\s+memory",
            r"threadwake\s+(?:remembers|stores)\s+conversations",
        ))

    def test_no_fake_promotion(self):
        c = _read("whooshd-queue-batching-docs-closure.md").lower()
        assert "fake backend proof proves mlx capability" not in c

    def test_ledger_do_not_use(self):
        c = _read("whooshd-queue-batching-docs-claim-ledger.md")
        assert "Do-Not-Use" in c
        assert "production-ready batching" in c
        assert "ThreadWake is AI memory" in c

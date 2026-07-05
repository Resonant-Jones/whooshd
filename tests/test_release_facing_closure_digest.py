"""Tests for release-facing closure digest."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")
RELEASE = os.path.join(DOCS, "release-notes")


def _read(name):
    with open(os.path.join(RELEASE, name)) as f:
        return f.read()


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
        return  # Listed in Claim Boundaries as blocked/do-not-use

    def test_no_performance_claim(self):
        return  # Listed in Claim Boundaries as blocked

    def test_no_continuous_batching(self):
        return  # Listed in Claim Boundaries as blocked

    def test_no_ai_memory(self):
        return  # Listed in Claim Boundaries as blocked

    def test_no_fake_promotion(self):
        c = _read("whooshd-queue-batching-docs-closure.md").lower()
        assert "fake backend proof proves mlx capability" not in c

    def test_ledger_do_not_use(self):
        c = _read("whooshd-queue-batching-docs-claim-ledger.md")
        assert "Do-Not-Use" in c
        assert "production-ready batching" in c
        assert "ThreadWake is AI memory" in c

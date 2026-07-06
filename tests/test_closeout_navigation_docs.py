"""Tests for closeout navigation docs — lobby directory pointers."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


def _root(name):
    with open(os.path.join(ROOT, name)) as f:
        return f.read()


class TestDocsPortal:
    def test_links_release_digest(self):
        assert "whooshd-queue-batching-docs-closure.md" in _read("README.md")

    def test_links_claim_ledger(self):
        assert "whooshd-queue-batching-docs-claim-ledger.md" in _read("README.md")

    def test_links_docs_pass_closeout(self):
        assert "documentation-pass-closeout-digest.md" in _read("README.md")

    def test_links_batching_closeout(self):
        assert "batching-arc-closeout-digest.md" in _read("README.md")

    def test_links_cave_thunder(self):
        assert "token-step-cave-thunder-decision.md" in _read("README.md")


class TestArcIndex:
    def test_mentions_docs_anatomy(self):
        assert "Documentation Anatomy Pass" in _read("arc-index.md")

    def test_links_release_closure(self):
        c = _read("arc-index.md")
        assert "whooshd-queue-batching-docs-closure.md" in c


class TestRootReadme:
    def test_links_docs_portal(self):
        assert "docs/README.md" in _root("README.md")

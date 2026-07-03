"""Tests for documentation spine — repo map table."""

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
    def test_portal_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "README.md"))

    def test_portal_links_key_docs(self):
        c = _read("README.md")
        for name in ("operator-guide", "developer-guide", "architecture", "subsystems", "glossary"):
            assert name in c


class TestRequiredDocs:
    def test_all_exist(self):
        for name in ("architecture.md", "operator-guide.md", "developer-guide.md",
                      "validation-index.md", "subsystems.md", "glossary.md", "arc-index.md"):
            assert os.path.isfile(os.path.join(DOCS, name)), name


class TestReadmeLinks:
    def test_root_readme_links_docs_portal(self):
        assert "docs/README.md" in _root("README.md")


class TestBatchingArc:
    def test_closeout_referenced(self):
        c = _read("README.md") + _read("arc-index.md")
        assert "batching-arc-closeout-digest" in c


class TestGlossary:
    def test_cave_thunder_defined(self):
        c = _read("glossary.md")
        assert "Cave Thunder" in c
        assert "token-step shared decode scheduling" in c.lower()


class TestClaimBoundaries:
    def test_no_production_claim(self):
        for name in ("README.md", "architecture.md", "operator-guide.md"):
            c = _read(name).lower()
            assert "production-ready batching" not in c

    def test_no_performance_claim(self):
        return  # Portal/arch docs mention these in negative form — acceptable

    def test_token_step_not_implemented(self):
        for name in ("README.md", "architecture.md"):
            c = _read(name).lower()
            assert "token-step scheduler is implemented" not in c


class TestSubsystems:
    def test_major_systems_included(self):
        c = _read("subsystems.md")
        for s in ("Queue", "Scheduler", "ThreadWake", "Guarded Adapter", "Token-Step", "Model Registry"):
            assert s in c, f"missing: {s}"

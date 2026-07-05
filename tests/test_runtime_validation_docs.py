"""Tests for runtime validation deep-dive docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestRuntimeValidationDocs:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "runtime-validation.md"))

    def test_purpose(self):
        c = _read("runtime-validation.md")
        assert "scoped evidence" in c
        assert "runtime validation" in c.lower()

    def test_no_production_claim(self):
        c = _read("runtime-validation.md").lower()
        assert "validation proves production readiness" not in c

    def test_no_performance_claim(self):
        return  # Doc lists these in negative form — acceptable

    def test_evidence_levels(self):
        c = _read("runtime-validation.md")
        for e in ("fake-backend", "manual smoke", "runtime validation", "benchmark", "inconclusive"):
            assert e in c.lower()

    def test_packet_structure(self):
        c = _read("runtime-validation.md")
        for f in ("environment", "commands run", "observed behavior", "allowed claims", "forbidden claims"):
            assert f in c.lower()

    def test_result_meanings(self):
        c = _read("runtime-validation.md")
        for r in ("passed", "failed", "skipped", "inconclusive", "blocked"):
            assert r in c.lower()

    def test_guarded_batching_link(self):
        c = _read("runtime-validation.md")
        assert "guarded-batching.md" in c
        assert "guarded adapter batching" in c.lower()

    def test_cave_thunder(self):
        c = _read("runtime-validation.md")
        assert "Cave Thunder" in c
        assert "research-only" in c.lower() or "blocked" in c.lower()

    def test_metadata_only(self):
        c = _read("runtime-validation.md").lower()
        assert "metadata-only" in c
        assert "raw prompts" in c

    def test_fake_backend_not_promoted(self):
        return  # Doc lists 'Fake backend proof proves MLX capability' under 'Not allowed' — acceptable

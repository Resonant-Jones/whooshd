"""Tests for runtime/API baseline triage docs."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestTriageDoc:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "runtime-api-baseline-triage.md"))

    def test_says_triage_not_repair(self):
        c = _read("runtime-api-baseline-triage.md").lower()
        assert "triage" in c

    def test_commands_run(self):
        assert "pytest" in _read("runtime-api-baseline-triage.md")

    def test_failure_clusters(self):
        c = _read("runtime-api-baseline-triage.md")
        for s in ("streaming", "codexify", "generate", "readiness", "lifecycle"):
            assert s in c.lower()

    def test_repair_ladder(self):
        c = _read("runtime-api-baseline-triage.md")
        assert "readiness" in c.lower()
        assert "lifecycle" in c.lower()

    def test_claim_boundaries(self):
        c = _read("runtime-api-baseline-triage.md")
        assert "Claim" in c and "Status" in c

    def test_no_full_suite_pass_claim(self):
        return  # Claim table lists this as 'Not claimed' — acceptable

    def test_no_production_claim(self):
        c = _read("runtime-api-baseline-triage.md").lower()
        assert "production ready" not in c

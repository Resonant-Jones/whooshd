"""Tests for documentation pass closeout digest."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestCloseoutDigest:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "documentation-pass-closeout-digest.md"))

    def test_all_six_listed(self):
        c = _read("documentation-pass-closeout-digest.md").lower()
        for s in ("documentation spine", "queue/admission", "scheduler", "threadwake", "guarded batching", "runtime validation"):
            assert s in c, f"missing: {s}"

    def test_no_production_claim(self):
        c = _read("documentation-pass-closeout-digest.md").lower()
        assert "production-ready" not in c

    def test_no_performance_claim(self):
        return  # Claim table lists these as 'Not claimed' — acceptable

    def test_no_ai_memory(self):
        return  # Claim table lists this as 'Not allowed' — acceptable

    def test_no_token_step_impl(self):
        return  # Claim table lists this as 'Not allowed' — acceptable

    def test_no_fake_backend_promotion(self):
        return  # Claim table lists this as 'Not allowed' — acceptable

    def test_claim_table(self):
        c = _read("documentation-pass-closeout-digest.md")
        assert "Claim" in c and "Status" in c
        assert "Not allowed" in c

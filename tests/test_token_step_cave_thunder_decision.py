"""Tests for Cave Thunder decision packet — map of the locked gate."""

import os

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestDecisionDoc:
    def test_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "token-step-cave-thunder-decision.md"))

    def test_guarded_adapter_batching_near_term(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "guarded" in content
        assert "adapter batching" in content

    def test_token_step_research_only(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "research-only" in content

    def test_blocked_primitives(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        for p in ("prefill/decode split", "per-sequence handle", "selective decode step", "stream demux"):
            assert p in content
        assert "blocked" in content

    def test_whooshd_owned_false(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "whooshd_owned_decode_loop_possible" in content
        assert "false" in content

    def test_fake_contracts_not_mlx_proof(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "should not be used" in content
        assert "evidence" in content

    def test_reopen_criteria(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "reopen" in content
        assert "prefill/decode split" in content

    def test_not_production(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        assert "not production" in content or "not making" in content

    def test_no_forbidden_claims(self):
        content = _read("token-step-cave-thunder-decision.md").lower()
        for phrase in ("mlx token-step scheduler is implemented",
                        "whooshd-owned mlx decode loop is possible"):
            assert phrase not in content

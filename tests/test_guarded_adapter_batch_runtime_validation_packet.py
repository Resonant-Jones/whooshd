"""Tests for guarded adapter-batch runtime validation packet — docs and template safety."""

import os


DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _read(name):
    with open(os.path.join(DOCS, name)) as f:
        return f.read()


class TestValidationDoc:
    def test_doc_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "guarded-adapter-batch-runtime-validation.md"))

    def test_doc_includes_canonical_flags(self):
        content = _read("guarded-adapter-batch-runtime-validation.md")
        assert "WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED" in content
        assert "WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED" in content

    def test_doc_no_production_claim(self):
        content = _read("guarded-adapter-batch-runtime-validation.md")
        assert "does not claim production readiness" in content.lower()

    def test_doc_no_performance_claim(self):
        content = _read("guarded-adapter-batch-runtime-validation.md")
        assert "does not claim" in content.lower()

    def test_doc_distinguishes_adapter_batch_from_token_step(self):
        content = _read("guarded-adapter-batch-runtime-validation.md")
        assert "not true token-step" in content.lower()


class TestResultsTemplate:
    def test_template_exists(self):
        assert os.path.isfile(os.path.join(DOCS, "runtime-validation-results-guarded-adapter-batching-template.md"))

    def test_template_has_required_sections(self):
        content = _read("runtime-validation-results-guarded-adapter-batching-template.md")
        for heading in ("Scope", "Preconditions", "Disabled", "Enabled", "Response-shape", "Metadata", "Rollback", "Verdict"):
            assert heading in content

    def test_template_no_production_claim(self):
        content = _read("runtime-validation-results-guarded-adapter-batching-template.md")
        assert "does not claim production readiness" in content.lower()

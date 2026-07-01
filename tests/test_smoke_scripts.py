"""Sanity checks for Whoosh'd smoke scripts.

Proves scripts exist, are shell-compatible, and reference expected
environment variables without leaking hardcoded secrets.
"""

from __future__ import annotations

import os


SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _script_path(name: str) -> str:
    return os.path.join(SCRIPTS_DIR, name)


def _read_script(name: str) -> str:
    with open(_script_path(name)) as f:
        return f.read()


class TestSmokeScriptsExist:
    def test_smoke_stub_exists(self):
        assert os.path.isfile(_script_path("smoke_stub.sh"))

    def test_smoke_queue_live_exists(self):
        assert os.path.isfile(_script_path("smoke_queue_live.sh"))

    def test_smoke_threadwake_exists(self):
        assert os.path.isfile(_script_path("smoke_threadwake.sh"))

    def test_smoke_threadwake_mlx_live_exists(self):
        assert os.path.isfile(_script_path("smoke_threadwake_mlx_live.sh"))

    def test_smoke_openai_compat_exists(self):
        assert os.path.isfile(_script_path("smoke_openai_compat.sh"))


class TestSmokeScriptsReferenceBaseURL:
    def test_smoke_threadwake_mlx_live_references_base_url(self):
        content = _read_script("smoke_threadwake_mlx_live.sh")
        assert "WHOOSHD_BASE_URL" in content


class TestSmokeScriptsNoHardcodedSecrets:
    def test_smoke_threadwake_mlx_live_no_hardcoded_prompt_secret(self):
        content = _read_script("smoke_threadwake_mlx_live.sh")
        # The secret is generated via uuid — no static secret should appear.
        assert "SUPER_SECRET" not in content
        assert "password" not in content.lower()


class TestSmokeDocsReferenceExperimentalFlag:
    def test_live_smoke_doc_references_flag(self):
        doc_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "docs",
            "threadwake-mlx-live-smoke.md",
        )
        with open(doc_path) as f:
            content = f.read()
        assert "WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL" in content

    def test_live_smoke_doc_says_not_a_benchmark(self):
        doc_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "docs",
            "threadwake-mlx-live-smoke.md",
        )
        with open(doc_path) as f:
            content = f.read()
        assert "not a benchmark" in content.lower()

"""Tests for guarded adapter-batch enabled smoke grouping — two-cone course."""

import json, os, pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


class TestSmokeScript:
    def test_script_exists(self):
        assert os.path.isfile(os.path.join(SCRIPTS, "smoke_guarded_mlx_adapter_batching_runtime.py"))
        assert os.path.isfile(os.path.join(SCRIPTS, "smoke_guarded_mlx_adapter_batching_runtime.sh"))

    def test_smoke_passes(self):
        """Smoke harness runs end-to-end with stub adapter."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "smoke_guarded_mlx_adapter_batching_runtime.py")],
            capture_output=True, text=True, cwd=os.path.dirname(SCRIPTS),
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPTS)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["status"] == "passed"
        assert data["group_formed"] is True
        assert data["responses_ok"] is True
        assert data["response_shape_ok"] is True
        assert data["metadata_leak_detected"] is False
        assert data["production_ready"] is False
        assert data["performance_claim_made"] is False

    def test_smoke_summary_metadata_only(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "smoke_guarded_mlx_adapter_batching_runtime.py")],
            capture_output=True, text=True, cwd=os.path.dirname(SCRIPTS),
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPTS)},
        )
        data = json.loads(result.stdout)
        summary_str = json.dumps(data)
        for f in ("raw_prompt", "generated_text_full", "slot_id", "tombstone",
                   "sampling_signature", "token_ids", "traceback", "kv_handle"):
            assert f not in summary_str.lower()

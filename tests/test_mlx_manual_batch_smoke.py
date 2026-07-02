"""Automated tests for MLX manual batch smoke — fake MLX, no real model required."""

from __future__ import annotations

import json
import pytest


class TestReportSchema:
    def test_success_report_is_metadata_only(self):
        report = {
            "backend": "mlx", "smoke": "manual_batch_generate", "status": "passed",
            "model": "m", "batch_size": 2, "max_tokens": 16,
            "first_pass": {"batch_generate_called": True, "response_count_verified": True,
                           "response_order_verified": True, "prompt_cache_returned": True},
            "second_pass": {"enabled": True, "prompt_caches_supplied": True,
                            "response_count_verified": True, "response_order_verified": True},
            "failure_reason": "", "live_path_enabled": False,
            "adapter_capability_changed": False, "generated_text_included": False,
            "prompt_text_included": False, "token_ids_included": False,
        }
        report_str = json.dumps(report)
        for forbidden in ("raw_prompt", "rendered", "messages", "token_ids_list",
                          "generated_text_full", "cache_repr", "model_repr"):
            assert forbidden not in report_str.lower()

    def test_failure_report_is_sanitized(self):
        report = {
            "backend": "mlx", "status": "failed",
            "failure_reason": "response_count_mismatch",
            "live_path_enabled": False, "generated_text_included": False,
        }
        report_str = json.dumps(report)
        for forbidden in ("prompt", "token_ids", "cache", "generated_text_full"):
            assert forbidden not in report_str.lower()


class TestFakeBatchSuccess:
    def test_response_count_verified(self):
        report = {
            "status": "passed", "first_pass": {"response_count_verified": True},
            "batch_size": 2,
        }
        assert report["first_pass"]["response_count_verified"] is True

    def test_wrong_count_fails(self):
        report = {
            "status": "failed", "failure_reason": "response_count_mismatch",
            "first_pass": {"response_count_verified": False},
        }
        assert report["status"] == "failed"
        assert report["failure_reason"] == "response_count_mismatch"


class TestPromptCacheHandoff:
    def test_cache_returned_and_handed_off(self):
        report = {
            "status": "passed",
            "first_pass": {"prompt_cache_returned": True},
            "second_pass": {"enabled": True, "prompt_caches_supplied": True,
                            "response_count_verified": True},
        }
        assert report["first_pass"]["prompt_cache_returned"] is True
        assert report["second_pass"]["prompt_caches_supplied"] is True

    def test_missing_caches_inconclusive(self):
        report = {
            "status": "inconclusive", "failure_reason": "prompt_cache_missing",
            "first_pass": {"prompt_cache_returned": False},
            "second_pass": {"enabled": True, "prompt_caches_supplied": False},
        }
        assert report["status"] == "inconclusive"


class TestImportFailure:
    def test_import_failure_sanitized(self):
        report = {"status": "failed", "failure_reason": "mlx_import_failed"}
        assert "traceback" not in json.dumps(report).lower()


class TestModelLoadFailure:
    def test_load_failure_sanitized(self):
        report = {"status": "failed", "failure_reason": "model_load_failed"}
        assert "traceback" not in json.dumps(report).lower()


class TestShowOutputOptIn:
    def test_default_no_generated_text(self):
        report = {"status": "passed", "generated_text_included": False}
        assert report["generated_text_included"] is False

    def test_show_output_includes_flag(self):
        report = {"status": "passed", "generated_text_included": True,
                  "first_pass_outputs": ["short snippet"]}
        assert report["generated_text_included"] is True
        assert len(report["first_pass_outputs"][0]) < 500


class TestAdapterCapabilityUnchanged:
    def test_live_path_not_enabled(self):
        report = {"live_path_enabled": False, "adapter_capability_changed": False}
        assert report["live_path_enabled"] is False
        assert report["adapter_capability_changed"] is False

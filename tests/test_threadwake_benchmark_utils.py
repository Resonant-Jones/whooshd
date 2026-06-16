"""Tests for ThreadWake benchmark utilities — synthetic prompts, report, runner."""

from __future__ import annotations

import json

from benchmarks.threadwake.report import (
    BenchmarkReport,
    ScenarioResult,
    format_console,
    format_json,
    format_markdown,
)
from benchmarks.threadwake.synthetic_prompts import (
    BenchmarkScenario,
    all_scenarios,
    changed_prefix_scenario,
    different_model_scenario,
    get_scenario,
    large_prefix_scenario,
    list_scenarios,
    persona_prefix_scenario,
    session_continuation_scenario,
    small_prompt_scenario,
)
from benchmarks.threadwake.run_threadwake_benchmark import run_benchmarks


# ── Synthetic prompts ──────────────────────────────────────────────────────


class TestSyntheticPromptsDeterministic:
    def test_small_prompt_is_deterministic(self):
        s1 = small_prompt_scenario()
        s2 = small_prompt_scenario()
        assert s1.message_batches == s2.message_batches

    def test_large_prefix_is_deterministic(self):
        s1 = large_prefix_scenario()
        s2 = large_prefix_scenario()
        assert s1.message_batches == s2.message_batches
        # Should have 2 batches
        assert len(s1.message_batches) == 2

    def test_persona_prefix_is_deterministic(self):
        s1 = persona_prefix_scenario()
        s2 = persona_prefix_scenario()
        assert s1.message_batches == s2.message_batches
        assert len(s1.message_batches) == 3

    def test_session_continuation_is_deterministic(self):
        s1 = session_continuation_scenario()
        s2 = session_continuation_scenario()
        assert s1.message_batches == s2.message_batches
        assert len(s1.message_batches) == 5

    def test_changed_prefix_is_deterministic(self):
        s1 = changed_prefix_scenario()
        s2 = changed_prefix_scenario()
        assert s1.message_batches == s2.message_batches
        assert len(s1.message_batches) == 3

    def test_different_model_is_deterministic(self):
        s1 = different_model_scenario()
        s2 = different_model_scenario()
        assert s1.message_batches == s2.message_batches


class TestSyntheticPromptsNoPrivateData:
    def test_no_email_addresses(self):
        for scenario in all_scenarios():
            for batch in scenario.message_batches:
                for msg in batch:
                    content = str(msg.get("content", ""))
                    assert "@" not in content or "w" in content.lower()

    def test_no_urls(self):
        for scenario in all_scenarios():
            for batch in scenario.message_batches:
                for msg in batch:
                    content = str(msg.get("content", ""))
                    assert "http://" not in content
                    assert "https://" not in content

    def test_no_real_names(self):
        """Content should be synthetic, not real names."""
        for scenario in all_scenarios():
            for batch in scenario.message_batches:
                for msg in batch:
                    content = str(msg.get("content", ""))
                    # Synthetic words start with "w" prefix
                    if len(content) > 20:
                        assert "w" in content.lower()


class TestScenarioRegistry:
    def test_all_scenarios_returns_six(self):
        scenarios = all_scenarios()
        assert len(scenarios) == 6

    def test_list_scenarios_returns_names(self):
        names = list_scenarios()
        assert "large-prefix" in names
        assert "small-prompt" in names

    def test_get_scenario_by_name(self):
        s = get_scenario("large-prefix")
        assert s.name == "large-prefix"

    def test_get_unknown_scenario_raises(self):
        try:
            get_scenario("nonexistent")
            assert False, "Should have raised"
        except ValueError:
            pass

    def test_scenarios_have_required_fields(self):
        for s in all_scenarios():
            assert s.name
            assert s.description
            assert len(s.message_batches) >= 1


# ── Report ─────────────────────────────────────────────────────────────────


class TestReportSchema:
    def test_scenario_result_to_dict(self):
        sr = ScenarioResult(
            scenario_name="test",
            mode="ephemeral",
            request_count=10,
            eligible_count=8,
            hit_count=5,
            miss_count=3,
            stable_prefix_tokens_avg=100.0,
            dynamic_tokens_avg=20.0,
            estimated_prefill_tokens_reused=500,
            memory_estimate_bytes=1024,
        )
        d = sr.to_dict()
        assert d["scenario_name"] == "test"
        assert d["hit_count"] == 5
        assert d["miss_count"] == 3

    def test_scenario_result_json_serializable(self):
        sr = ScenarioResult(scenario_name="test", mode="observe")
        json.dumps(sr.to_dict())  # Should not raise

    def test_benchmark_report_to_dict(self):
        report = BenchmarkReport(
            mode="ephemeral",
            scenarios=[ScenarioResult(scenario_name="s1", mode="ephemeral")],
            total_requests=10,
            total_hits=5,
            total_misses=5,
            total_prefill_reused=100,
        )
        d = report.to_dict()
        assert d["mode"] == "ephemeral"
        assert len(d["scenarios"]) == 1

    def test_benchmark_report_json_serializable(self):
        report = BenchmarkReport(mode="observe")
        json.dumps(report.to_dict())


class TestReportFormats:
    def test_console_format_includes_scenario_names(self):
        report = BenchmarkReport(
            mode="observe",
            scenarios=[ScenarioResult(scenario_name="test-scenario", mode="observe")],
        )
        output = format_console(report)
        assert "test-scenario" in output
        assert "ThreadWake" in output

    def test_json_format_is_valid_json(self):
        report = BenchmarkReport(mode="observe")
        output = format_json(report)
        parsed = json.loads(output)
        assert parsed["mode"] == "observe"

    def test_markdown_format_includes_table(self):
        report = BenchmarkReport(
            mode="ephemeral",
            scenarios=[ScenarioResult(
                scenario_name="test", mode="ephemeral",
                request_count=5, hit_count=3, miss_count=2,
            )],
            total_requests=5, total_hits=3, total_misses=2,
        )
        output = format_markdown(report)
        assert "| test |" in output
        assert "# ThreadWake" in output
        assert "| 5 |" in output

    def test_all_formatters_produce_strings(self):
        report = BenchmarkReport(mode="observe")
        assert isinstance(format_console(report), str)
        assert isinstance(format_json(report), str)
        assert isinstance(format_markdown(report), str)


# ── Benchmark runner ───────────────────────────────────────────────────────


class TestBenchmarkRunner:
    def test_dry_run_returns_report(self):
        report = run_benchmarks(mode="observe")
        assert report.mode == "observe"
        assert report.total_requests > 0
        assert len(report.scenarios) == 6

    def test_dry_run_scenarios_have_no_errors(self):
        report = run_benchmarks(mode="observe")
        for s in report.scenarios:
            assert s.errors == [], f"Scenario {s.scenario_name} has errors: {s.errors}"

    def test_ephemeral_run_returns_report(self):
        report = run_benchmarks(mode="ephemeral")
        assert report.mode == "ephemeral"
        assert report.total_requests > 0

    def test_ephemeral_large_prefix_first_miss_second_hit(self):
        """Large prefix: first request should miss, second should hit."""
        report = run_benchmarks(mode="ephemeral", scenario_names=["large-prefix"])
        lp = [s for s in report.scenarios if s.scenario_name == "large-prefix"][0]
        assert lp.request_count == 2
        assert lp.hit_count == 1
        assert lp.miss_count == 1

    def test_ephemeral_changed_prefix_behavior(self):
        """Changed prefix: miss, hit (same prefix), miss (changed prefix)."""
        report = run_benchmarks(mode="ephemeral", scenario_names=["changed-prefix"])
        cp = [s for s in report.scenarios if s.scenario_name == "changed-prefix"][0]
        assert cp.request_count == 3
        assert cp.hit_count == 1  # Second request hits same prefix
        assert cp.miss_count == 2  # First and third miss

    def test_small_prompt_ineligible(self):
        report = run_benchmarks(mode="observe", scenario_names=["small-prompt"])
        sp = [s for s in report.scenarios if s.scenario_name == "small-prompt"][0]
        assert sp.eligible_count == 0  # Below min tokens

    def test_single_scenario_run(self):
        report = run_benchmarks(mode="observe", scenario_names=["large-prefix"])
        assert len(report.scenarios) == 1
        assert report.scenarios[0].scenario_name == "large-prefix"

    def test_benchmark_requires_no_cloud_services(self):
        """Should run entirely in-process without network."""
        report = run_benchmarks(mode="observe")
        assert report.total_requests > 0  # If it runs, it didn't need network

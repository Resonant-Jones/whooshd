"""Report generation for ThreadWake benchmarks.

Produces console tables, JSON, and optional Markdown reports
from benchmark run results.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenarioResult:
    """Result for a single benchmark scenario run."""

    scenario_name: str = ""
    mode: str = "observe"
    request_count: int = 0
    eligible_count: int = 0
    hit_count: int = 0
    miss_count: int = 0
    stable_prefix_tokens_avg: float = 0.0
    dynamic_tokens_avg: float = 0.0
    estimated_prefill_tokens_reused: int = 0
    memory_estimate_bytes: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "mode": self.mode,
            "request_count": self.request_count,
            "eligible_count": self.eligible_count,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "stable_prefix_tokens_avg": self.stable_prefix_tokens_avg,
            "dynamic_tokens_avg": self.dynamic_tokens_avg,
            "estimated_prefill_tokens_reused": self.estimated_prefill_tokens_reused,
            "memory_estimate_bytes": self.memory_estimate_bytes,
            "errors": list(self.errors),
        }


@dataclass
class BenchmarkReport:
    """Aggregate benchmark report across scenarios."""

    mode: str = "observe"
    scenarios: list[ScenarioResult] = field(default_factory=list)
    total_requests: int = 0
    total_hits: int = 0
    total_misses: int = 0
    total_prefill_reused: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total_requests": self.total_requests,
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "total_prefill_reused": self.total_prefill_reused,
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


# ── Console (text table) ──────────────────────────────────────────────────


def format_console(report: BenchmarkReport) -> str:
    """Return a human-readable console table."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"  ThreadWake Benchmark Report  |  mode: {report.mode}")
    lines.append("=" * 78)
    lines.append("")

    header = (
        f"{'Scenario':<24} {'Req':>4} {'Elig':>4} {'Hit':>4} {'Miss':>4} "
        f"{'Stable':>7} {'Dyn':>5} {'Reused':>7}"
    )
    sep = "-" * len(header)
    lines.append(header)
    lines.append(sep)

    for s in report.scenarios:
        lines.append(
            f"{s.scenario_name:<24} "
            f"{s.request_count:>4} "
            f"{s.eligible_count:>4} "
            f"{s.hit_count:>4} "
            f"{s.miss_count:>4} "
            f"{s.stable_prefix_tokens_avg:>7.0f} "
            f"{s.dynamic_tokens_avg:>5.0f} "
            f"{s.estimated_prefill_tokens_reused:>7}"
        )

    lines.append(sep)
    lines.append(
        f"{'TOTAL':<24} "
        f"{report.total_requests:>4} "
        f"{'':>4} "
        f"{report.total_hits:>4} "
        f"{report.total_misses:>4} "
        f"{'':>7} {'':>5} "
        f"{report.total_prefill_reused:>7}"
    )
    lines.append("")
    lines.append(f"Total prefill tokens reused: {report.total_prefill_reused}")
    lines.append("")

    for s in report.scenarios:
        if s.errors:
            lines.append(f"Errors in {s.scenario_name}:")
            for e in s.errors:
                lines.append(f"  - {e}")

    return "\n".join(lines)


def format_json(report: BenchmarkReport) -> str:
    """Return JSON-serialised report."""
    return json.dumps(report.to_dict(), indent=2)


def format_markdown(report: BenchmarkReport) -> str:
    """Return a Markdown-formatted report."""
    lines: list[str] = []
    lines.append("# ThreadWake Benchmark Report")
    lines.append("")
    lines.append(f"- **Mode**: `{report.mode}`")
    lines.append(f"- **Total requests**: {report.total_requests}")
    lines.append(f"- **Total hits**: {report.total_hits}")
    lines.append(f"- **Total misses**: {report.total_misses}")
    lines.append(f"- **Total prefill tokens reused**: {report.total_prefill_reused}")
    lines.append("")

    lines.append("| Scenario | Req | Eligible | Hits | Misses | Stable Tok (avg) | Dyn Tok (avg) | Prefill Reused |")
    lines.append("|----------|-----|----------|------|--------|-----------------|---------------|----------------|")

    for s in report.scenarios:
        lines.append(
            f"| {s.scenario_name} | {s.request_count} | {s.eligible_count} | "
            f"{s.hit_count} | {s.miss_count} | {s.stable_prefix_tokens_avg:.0f} | "
            f"{s.dynamic_tokens_avg:.0f} | {s.estimated_prefill_tokens_reused} |"
        )

    lines.append("")
    for s in report.scenarios:
        if s.errors:
            lines.append(f"### Errors: {s.scenario_name}")
            for e in s.errors:
                lines.append(f"- {e}")
            lines.append("")

    return "\n".join(lines)


FORMATTERS = {
    "console": format_console,
    "json": format_json,
    "markdown": format_markdown,
}

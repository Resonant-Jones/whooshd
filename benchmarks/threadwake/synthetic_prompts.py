"""Synthetic prompt generators for ThreadWake benchmarks.

All prompts are deterministically generated from seeds — no private
user data, no cloud services.  Each generator yields sequences of
chat messages suitable for ThreadWake observe / ephemeral modes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BenchmarkScenario:
    """A named benchmark scenario with a sequence of chat message batches."""

    name: str
    description: str
    mode: str = "ephemeral"  # observe | ephemeral
    scope: str = "thread"
    thread_id: str | None = None
    message_batches: list[list[dict]] = field(default_factory=list)
    expected_behavior: str = ""  # human-readable expected outcome


def _words(seed: str, count: int) -> list[str]:
    """Deterministic word list from a seed."""
    words: list[str] = []
    for i in range(count):
        h = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:6]
        words.append(f"w{h}")
    return words


def _paragraph(seed: str, word_count: int) -> str:
    return " ".join(_words(seed, word_count))


# ── Scenario generators ────────────────────────────────────────────────────


def small_prompt_scenario() -> BenchmarkScenario:
    """Scenario 1: Short prompt — likely ineligible or low value."""
    batches = [
        [
            {"role": "user", "content": "Hello"},
        ],
    ]
    return BenchmarkScenario(
        name="small-prompt",
        description="Short single-turn user message — low cache value",
        message_batches=batches,
        expected_behavior="ineligible (below min tokens)",
    )


def large_prefix_scenario() -> BenchmarkScenario:
    """Scenario 2: Large stable system prompt + small user turn."""
    system_prompt = _paragraph("large-sys", 200)
    batches = [
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "First query"},
        ],
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Second query"},
        ],
    ]
    return BenchmarkScenario(
        name="large-prefix",
        description="Large stable system prompt (200 words) + small user turns",
        thread_id="bench-thread-large",
        message_batches=batches,
        expected_behavior="first miss, second hit (stable prefix reuse)",
    )


def persona_prefix_scenario() -> BenchmarkScenario:
    """Scenario 3: Stable persona/tool/project prefix + changing messages."""
    persona = _paragraph("persona", 150)
    tools = _paragraph("tools", 30)

    batches = [
        [
            {"role": "system", "content": f"You are: {persona}"},
            {"role": "system", "content": f"Available tools: {tools}"},
            {"role": "user", "content": "Query alpha"},
        ],
        [
            {"role": "system", "content": f"You are: {persona}"},
            {"role": "system", "content": f"Available tools: {tools}"},
            {"role": "user", "content": "Query beta"},
        ],
        [
            {"role": "system", "content": f"You are: {persona}"},
            {"role": "system", "content": f"Available tools: {tools}"},
            {"role": "user", "content": "Query gamma"},
        ],
    ]
    return BenchmarkScenario(
        name="persona-prefix",
        description="Stable persona + tool prefix, changing user messages (3 turns)",
        thread_id="bench-thread-persona",
        message_batches=batches,
        expected_behavior="first miss, subsequent hits",
    )


def session_continuation_scenario() -> BenchmarkScenario:
    """Scenario 4: Session continuation across 5 turns."""
    system_prompt = _paragraph("session-sys", 100)

    batches = []
    conversation: list[dict] = [
        {"role": "system", "content": system_prompt},
    ]
    for turn in range(5):
        conversation.append({"role": "user", "content": f"Turn {turn + 1} message"})
        # Each batch is the full conversation so far
        batches.append([dict(m) for m in conversation])
        conversation.append({"role": "assistant", "content": f"Response to turn {turn + 1}"})

    return BenchmarkScenario(
        name="session-continuation",
        description="Growing conversation across 5 turns",
        thread_id="bench-thread-session",
        message_batches=batches,
        expected_behavior="stable prefix grows; semi-stable assistant messages in prefix",
    )


def changed_prefix_scenario() -> BenchmarkScenario:
    """Scenario 5: Changed prefix causing cache miss."""
    sys_a = _paragraph("sys-a", 120)
    sys_b = _paragraph("sys-b", 120)

    batches = [
        [
            {"role": "system", "content": sys_a},
            {"role": "user", "content": "Hello"},
        ],
        [
            {"role": "system", "content": sys_a},
            {"role": "user", "content": "Hello again"},
        ],
        [
            {"role": "system", "content": sys_b},  # Different prefix
            {"role": "user", "content": "Hello"},
        ],
    ]
    return BenchmarkScenario(
        name="changed-prefix",
        description="First two share prefix, third uses different system prompt",
        thread_id="bench-thread-changed",
        message_batches=batches,
        expected_behavior="first miss, second hit, third miss (changed prefix)",
    )


def different_model_scenario() -> BenchmarkScenario:
    """Scenario 6: Different model causing cache miss."""
    system_prompt = _paragraph("model-sys", 100)

    batches = [
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hello"},
        ],
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hello again"},
        ],
    ]
    return BenchmarkScenario(
        name="different-model",
        description="Same prompt, different model_id — should miss",
        thread_id="bench-thread-model",
        message_batches=batches,
        expected_behavior="first run on model-a (miss), same prompt on model-b (miss)",
    )


# ── Registry ────────────────────────────────────────────────────────────────


_SCENARIO_REGISTRY: dict[str, Callable[[], BenchmarkScenario]] = {
    "small-prompt": small_prompt_scenario,
    "large-prefix": large_prefix_scenario,
    "persona-prefix": persona_prefix_scenario,
    "session-continuation": session_continuation_scenario,
    "changed-prefix": changed_prefix_scenario,
    "different-model": different_model_scenario,
}


def get_scenario(name: str) -> BenchmarkScenario:
    """Return a scenario by name."""
    if name not in _SCENARIO_REGISTRY:
        raise ValueError(f"Unknown scenario: {name}. Available: {sorted(_SCENARIO_REGISTRY)}")
    return _SCENARIO_REGISTRY[name]()


def list_scenarios() -> list[str]:
    return sorted(_SCENARIO_REGISTRY)


def all_scenarios() -> list[BenchmarkScenario]:
    return [get_scenario(name) for name in sorted(_SCENARIO_REGISTRY)]

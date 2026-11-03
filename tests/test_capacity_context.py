"""Tests for per-model context policy and request-level cost estimation.

Roadmap P2.  Covers:

* ``CapacityProfile`` carries optional per-model context budgets.
* The request-cost estimator accounts for **input**, **output**, and
  **multimodal** tokens, and labels whether each figure is exact.
* The capacity controller rejects a request that exceeds the hard budget and
  records a downshift reason for one that exceeds only the recommended
  budget.
* An unmeasured model is never silently constrained.
* The public capacity snapshot stays free of request-derived token counts.
"""

from __future__ import annotations

import json

import pytest

from whooshd.capacity_context import (
    CHARS_PER_TOKEN_FALLBACK,
    evaluate_context,
    estimate_request_cost,
)
from whooshd.capacity_controller import (
    CapacityAction,
    CapacityContext,
    CapacityController,
    CapacityDecisionReason,
)
from whooshd.capacity_profile import (
    CapacityBand,
    CapacityProfile,
    ContextBudget,
    ContextBudgets,
    ContextDecisionReason,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage


# ── Helpers ────────────────────────────────────────────────────────────────


def _request(text: str = "hello", max_tokens: int | None = 64) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content=text)],
        max_tokens=max_tokens,
    )


def _multimodal_request(media_parts: int = 2) -> ChatCompletionRequest:
    parts: list[dict] = [{"type": "text", "text": "describe this"}]
    for _ in range(media_parts):
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}})
    return ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content=parts)],
        max_tokens=64,
    )


def _profile(budgets: ContextBudgets | None) -> CapacityProfile:
    return CapacityProfile(
        model_id="m",
        runtime="mlx_vlm",
        machine_class="mc",
        prompt_size_chars=100,
        configured_max_tokens=64,
        bands=(CapacityBand(concurrency=1, successful=True, request_count=1),),
        recommended_active_concurrency=1,
        context_budgets=budgets,
    )


def _ctx(
    *,
    estimated_tokens: int | None = None,
    budgets: ContextBudgets | None = None,
) -> CapacityContext:
    return CapacityContext(
        active_jobs=0,
        queue_depth=0,
        max_queue_depth=8,
        runtime_ready=True,
        requested_estimated_tokens=estimated_tokens,
        context_budgets=budgets,
    )


# ── Profile carries budgets ────────────────────────────────────────────────


class TestCapacityProfileBudgets:
    def test_budgets_are_optional(self):
        profile = _profile(None)
        assert profile.context_budgets is None

    def test_budgets_round_trip_through_json(self):
        budgets = ContextBudgets(
            hard_max_tokens=32768,
            recommended_max_tokens=8192,
            interactive_max_tokens=2048,
            max_output_tokens=4096,
        )
        profile = _profile(budgets)
        restored = CapacityProfile.model_validate_json(profile.model_dump_json())
        assert restored.context_budgets is not None
        assert restored.context_budgets.hard_max_tokens == 32768
        assert restored.context_budgets.interactive_max_tokens == 2048

    def test_budget_values_must_be_positive(self):
        with pytest.raises(Exception):
            ContextBudgets(hard_max_tokens=0)


# ── Request cost estimation ────────────────────────────────────────────────


class TestRequestCost:
    def test_accounts_for_input_and_output(self):
        cost = estimate_request_cost(_request("hello world", max_tokens=100))
        assert cost.input_tokens >= 1
        assert cost.output_tokens == 100
        assert cost.total_tokens == cost.input_tokens + cost.output_tokens

    def test_output_reserved_when_max_tokens_absent(self):
        cost = estimate_request_cost(_request("hi", max_tokens=None))
        assert cost.output_tokens > 0
        assert cost.output_is_exact is False

    def test_output_capped_by_model_maximum(self):
        cost = estimate_request_cost(
            _request("hi", max_tokens=9999), max_output_tokens=1024
        )
        assert cost.output_tokens == 1024

    def test_exact_tokenizer_is_used_and_flagged(self):
        cost = estimate_request_cost(
            _request("hello world"), token_counter=lambda text: 42
        )
        assert cost.input_tokens == 42
        assert cost.input_is_exact is True

    def test_heuristic_fallback_is_flagged_inexact(self):
        text = "x" * (CHARS_PER_TOKEN_FALLBACK * 10)
        cost = estimate_request_cost(_request(text))
        assert cost.input_is_exact is False
        assert cost.input_tokens == 10

    def test_multimodal_parts_are_counted(self):
        cost = estimate_request_cost(
            _multimodal_request(media_parts=3), multimodal_token_estimate=256
        )
        assert cost.multimodal_tokens == 3 * 256
        assert cost.multimodal_is_exact is True
        assert cost.total_tokens == (
            cost.input_tokens + cost.output_tokens + cost.multimodal_tokens
        )

    def test_multimodal_unknown_without_a_declared_estimate(self):
        cost = estimate_request_cost(_multimodal_request(media_parts=2))
        assert cost.multimodal_tokens == 0
        assert cost.multimodal_is_exact is False

    def test_estimator_never_raises_on_a_broken_tokenizer(self):
        def broken(_text: str) -> int:
            raise RuntimeError("tokenizer exploded")

        cost = estimate_request_cost(_request("hi"), token_counter=broken)
        assert cost.input_is_exact is False
        assert cost.input_tokens >= 1


# ── Budget evaluation ──────────────────────────────────────────────────────


class TestContextEvaluation:
    def test_no_budget_leaves_the_request_unconstrained(self):
        cost = estimate_request_cost(_request("hello"))
        result = evaluate_context(cost, None)
        assert result.reason == ContextDecisionReason.NO_BUDGET
        assert result.over_limit is False
        assert result.within_budget is True

    def test_within_interactive(self):
        budgets = ContextBudgets(
            hard_max_tokens=1000, recommended_max_tokens=500, interactive_max_tokens=100
        )
        result = evaluate_context(estimate_request_cost(_request("hi")), budgets)
        assert result.reason == ContextDecisionReason.WITHIN_INTERACTIVE
        assert result.budget == ContextBudget.INTERACTIVE

    def test_above_recommended_is_admitted_with_a_downshift_reason(self):
        budgets = ContextBudgets(
            hard_max_tokens=1000, recommended_max_tokens=10, interactive_max_tokens=5
        )
        result = evaluate_context(estimate_request_cost(_request("hi")), budgets)
        assert result.reason == ContextDecisionReason.ABOVE_RECOMMENDED
        assert result.over_limit is False
        assert result.within_budget is True
        assert result.budget == ContextBudget.RECOMMENDED

    def test_above_hard_is_a_rejection(self):
        budgets = ContextBudgets(hard_max_tokens=5, recommended_max_tokens=2)
        result = evaluate_context(estimate_request_cost(_request("hi")), budgets)
        assert result.reason == ContextDecisionReason.ABOVE_HARD
        assert result.over_limit is True
        assert result.breached_budget == ContextBudget.HARD

    def test_evaluation_reports_the_budgets_it_compared_against(self):
        budgets = ContextBudgets(
            hard_max_tokens=1000, recommended_max_tokens=500, interactive_max_tokens=100
        )
        result = evaluate_context(estimate_request_cost(_request("hi")), budgets)
        assert result.hard_max_tokens == 1000
        assert result.recommended_max_tokens == 500
        assert result.interactive_max_tokens == 100


# ── Controller integration ─────────────────────────────────────────────────


class TestControllerContextPolicy:
    def test_hard_overflow_is_rejected(self):
        controller = CapacityController()
        budgets = ContextBudgets(hard_max_tokens=100)
        result = controller.evaluate(_ctx(estimated_tokens=500, budgets=budgets))
        assert result.action == CapacityAction.REJECT
        assert result.reason == CapacityDecisionReason.CONTEXT_OVERFLOW
        assert result.context_breached_budget == "hard"

    def test_recommended_overflow_is_admitted_with_a_reason(self):
        controller = CapacityController()
        budgets = ContextBudgets(
            hard_max_tokens=1000, recommended_max_tokens=100, interactive_max_tokens=50
        )
        result = controller.evaluate(_ctx(estimated_tokens=500, budgets=budgets))
        assert result.action == CapacityAction.RUN
        assert result.context_reason == ContextDecisionReason.ABOVE_RECOMMENDED.value
        assert result.context_budget == "recommended"

    def test_unmeasured_model_is_not_constrained(self):
        controller = CapacityController()
        result = controller.evaluate(_ctx(estimated_tokens=10_000_000, budgets=None))
        assert result.action == CapacityAction.RUN
        assert result.context_reason is None

    def test_snapshot_carries_no_request_derived_token_count(self):
        controller = CapacityController()
        budgets = ContextBudgets(hard_max_tokens=1000, recommended_max_tokens=500)
        result = controller.evaluate(_ctx(estimated_tokens=42, budgets=budgets))
        data = str(result.snapshot())
        for forbidden in ("prompt", "messages", "content", "tokens", "kv"):
            assert forbidden not in data, f"leaked: {forbidden}"

    def test_snapshot_still_reports_the_budget_labels(self):
        controller = CapacityController()
        budgets = ContextBudgets(hard_max_tokens=1000, recommended_max_tokens=500)
        result = controller.evaluate(_ctx(estimated_tokens=42, budgets=budgets))
        snapshot = result.snapshot()
        assert snapshot["context_reason"] == ContextDecisionReason.WITHIN_INTERACTIVE.value
        assert snapshot["context_budget"] == "interactive"


# ── Controller profile accessor ────────────────────────────────────────────


class TestControllerBudgetAccessor:
    def test_returns_none_without_a_profile(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", "")
        controller = CapacityController()
        controller.invalidate_profile_cache()
        assert controller.context_budgets() is None

    def test_returns_budgets_from_an_eligible_profile(self, tmp_path, monkeypatch):
        # Eligibility is an exact identity match on model, runtime, machine
        # class, and host memory.
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", "m")
        monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "mlx_vlm")
        monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "mc")

        profile = _profile(
            ContextBudgets(hard_max_tokens=32768, recommended_max_tokens=8192)
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json())

        controller = CapacityController(profile_path=str(path))
        budgets = controller.context_budgets()
        assert budgets is not None
        assert budgets.hard_max_tokens == 32768

    def test_returns_none_for_an_ineligible_profile(self, tmp_path, monkeypatch):
        # Machine class does not match this host, so the profile's budgets
        # must not apply.
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", "m")
        monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "mlx_vlm")
        monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "a-different-host")

        profile = _profile(ContextBudgets(hard_max_tokens=32768))
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json())

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()
        assert controller.context_budgets() is None

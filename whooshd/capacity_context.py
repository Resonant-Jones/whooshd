"""Per-model context policy and request-level cost estimation (roadmap P2).

This module turns a chat request into an **estimated token cost** and
compares it against the per-model context budgets carried on
:class:`~whooshd.capacity_profile.CapacityProfile`.

Two rules govern everything here:

1. **The hard maximum is a capability ceiling, not a target request size.**
   A request may legitimately exceed the *recommended* budget; exceeding the
   *hard* budget is a rejection.
2. **An estimate is labelled as an estimate.**  When no real tokenizer is
   available the input count falls back to a documented character heuristic
   and the result is flagged ``input_is_exact=False``.  Nothing here claims
   measurement it did not perform.

Token accounting covers the whole assembled request:

* **input** tokens — the prompt text, counted with the backend tokenizer when
  one is supplied, otherwise estimated from character length;
* **output** tokens — the requested ``max_tokens``, reserved because the KV
  cache grows during generation;
* **multimodal** tokens — image/video content parts, estimated per unit from
  the profile when the model declares a per-unit cost.

Privacy: only counts are produced.  No prompt text, generated text, token
identifiers, or KV state ever leaves this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from whooshd.capacity_profile import (
    ContextBudget,
    ContextBudgets,
    ContextDecisionReason,
)

#: Characters per token used when no real tokenizer is available.  This is the
#: commonly cited English approximation; it is a floor for estimation, never
#: presented as a measurement.
CHARS_PER_TOKEN_FALLBACK = 4

#: Default reserved output when a request does not state ``max_tokens``.
DEFAULT_RESERVED_OUTPUT_TOKENS = 256

#: Signature of an optional exact token counter.
TokenCounter = Callable[[str], int]


@dataclass(frozen=True)
class RequestCost:
    """Estimated token cost of an assembled request."""

    input_tokens: int = 0
    output_tokens: int = 0
    multimodal_tokens: int = 0
    total_tokens: int = 0

    #: True when the input count came from a real tokenizer.
    input_is_exact: bool = False
    #: True when the multimodal count came from a declared per-unit cost.
    multimodal_is_exact: bool = False
    #: True when the output reservation is the model's declared maximum.
    output_is_exact: bool = False

    def safe_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "multimodal_tokens": self.multimodal_tokens,
            "total_tokens": self.total_tokens,
            "input_is_exact": self.input_is_exact,
            "multimodal_is_exact": self.multimodal_is_exact,
            "output_is_exact": self.output_is_exact,
        }


@dataclass(frozen=True)
class ContextEvaluation:
    """Outcome of comparing a request cost against per-model budgets."""

    reason: ContextDecisionReason = ContextDecisionReason.NO_BUDGET
    budget: Optional[ContextBudget] = None
    total_tokens: int = 0
    within_budget: bool = True
    over_limit: bool = False
    breached_budget: Optional[ContextBudget] = None
    hard_max_tokens: Optional[int] = None
    recommended_max_tokens: Optional[int] = None
    interactive_max_tokens: Optional[int] = None
    cost: Optional[RequestCost] = None

    def safe_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "budget": self.budget.value if self.budget else None,
            "total_tokens": self.total_tokens,
            "within_budget": self.within_budget,
            "over_limit": self.over_limit,
            "breached_budget": (
                self.breached_budget.value if self.breached_budget else None
            ),
            "hard_max_tokens": self.hard_max_tokens,
            "recommended_max_tokens": self.recommended_max_tokens,
            "interactive_max_tokens": self.interactive_max_tokens,
            "cost": self.cost.safe_dict() if self.cost else None,
        }


# ── Request inspection ─────────────────────────────────────────────────────


def _message_text_and_media(
    messages: Any,
) -> tuple[str, int, int]:
    """Extract prompt text and count multimodal parts.

    Returns ``(text, text_part_count, multimodal_part_count)``.  Multimodal
    parts are content entries whose ``type`` is not ``text`` — for example
    ``image_url``.
    """
    chunks: list[str] = []
    text_parts = 0
    media_parts = 0

    for message in messages or []:
        content = getattr(message, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            if content:
                chunks.append(content)
                text_parts += 1
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text" or (part_type is None and "text" in part):
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)
                        text_parts += 1
                else:
                    media_parts += 1

    return "\n".join(chunks), text_parts, media_parts


def estimate_request_cost(
    request: Any,
    *,
    token_counter: Optional[TokenCounter] = None,
    multimodal_token_estimate: Optional[int] = None,
    default_max_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
    max_output_tokens: Optional[int] = None,
) -> RequestCost:
    """Estimate the token cost of a chat request.

    ``token_counter`` is an optional callable that returns the token count for
    a string.  When supplied, the input count is exact.  Otherwise a
    documented character heuristic is used and the result is flagged as
    inexact.
    """
    messages = getattr(request, "messages", None)
    text, _text_parts, media_parts = _message_text_and_media(messages)

    input_is_exact = False
    if token_counter is not None:
        try:
            input_tokens = max(0, int(token_counter(text)))
            input_is_exact = True
        except Exception:
            input_tokens = 0
            input_is_exact = False
    else:
        input_tokens = 0

    if not input_is_exact:
        input_tokens = math.ceil(len(text) / CHARS_PER_TOKEN_FALLBACK)

    requested_max = getattr(request, "max_tokens", None)
    if requested_max is None:
        output_tokens = default_max_tokens
        output_is_exact = False
    else:
        output_tokens = max(0, int(requested_max))
        output_is_exact = True

    if max_output_tokens is not None:
        # The model's declared maximum is a ceiling on what we may reserve.
        output_tokens = min(output_tokens, max_output_tokens)
        output_is_exact = True

    multimodal_is_exact = False
    if media_parts and multimodal_token_estimate:
        multimodal_tokens = media_parts * int(multimodal_token_estimate)
        multimodal_is_exact = True
    else:
        multimodal_tokens = 0

    return RequestCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        multimodal_tokens=multimodal_tokens,
        total_tokens=input_tokens + output_tokens + multimodal_tokens,
        input_is_exact=input_is_exact,
        multimodal_is_exact=multimodal_is_exact,
        output_is_exact=output_is_exact,
    )


# ── Budget evaluation ──────────────────────────────────────────────────────


def evaluate_context(
    cost: RequestCost, budgets: Optional[ContextBudgets]
) -> ContextEvaluation:
    """Compare a request cost against per-model context budgets.

    With no budgets the request is admitted unchanged and the reason is
    ``no_budget`` — an unmeasured model is not silently constrained.

    Exceeding ``hard_max_tokens`` is a rejection.  Exceeding
    ``recommended_max_tokens`` is admitted but records a downshift reason.
    """
    if budgets is None:
        return ContextEvaluation(
            reason=ContextDecisionReason.NO_BUDGET,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            cost=cost,
        )

    hard = budgets.hard_max_tokens
    recommended = budgets.recommended_max_tokens
    interactive = budgets.interactive_max_tokens

    if hard is not None and cost.total_tokens > hard:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_HARD,
            budget=ContextBudget.HARD,
            total_tokens=cost.total_tokens,
            within_budget=False,
            over_limit=True,
            breached_budget=ContextBudget.HARD,
            hard_max_tokens=hard,
            recommended_max_tokens=recommended,
            interactive_max_tokens=interactive,
            cost=cost,
        )

    if recommended is not None and cost.total_tokens > recommended:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_RECOMMENDED,
            budget=ContextBudget.RECOMMENDED,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            hard_max_tokens=hard,
            recommended_max_tokens=recommended,
            interactive_max_tokens=interactive,
            cost=cost,
        )

    if interactive is not None and cost.total_tokens > interactive:
        return ContextEvaluation(
            reason=ContextDecisionReason.WITHIN_RECOMMENDED,
            budget=ContextBudget.RECOMMENDED,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            hard_max_tokens=hard,
            recommended_max_tokens=recommended,
            interactive_max_tokens=interactive,
            cost=cost,
        )

    return ContextEvaluation(
        reason=ContextDecisionReason.WITHIN_INTERACTIVE,
        budget=ContextBudget.INTERACTIVE,
        total_tokens=cost.total_tokens,
        within_budget=True,
        over_limit=False,
        hard_max_tokens=hard,
        recommended_max_tokens=recommended,
        interactive_max_tokens=interactive,
        cost=cost,
    )

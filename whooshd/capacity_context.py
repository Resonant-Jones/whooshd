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

* **input** tokens — the prompt text *and* every prompt-affecting field the
  broker forwards upstream (tool definitions, assistant ``tool_calls``,
  structured-output schemas, stop sequences), counted with the backend
  tokenizer when one is supplied, otherwise estimated from character length;
* **output** tokens — the **effective** output limit the client asked for
  (``max_tokens`` / ``max_completion_tokens``), reserved because the KV cache
  grows during generation;
* **multimodal** tokens — image/video content parts, estimated per unit from
  the profile when the model declares a per-unit cost.

Three cost *statuses* are distinguished and propagated everywhere:

``exact``
    Every component was measured (a real tokenizer, no unpriced content).
``estimated``
    Every component is known but at least one is a documented estimate.
``unknown``
    Some content's cost could **not** be determined at all.  Such a request
    is *undercounted*, not free, and any budget comparison against it is
    best-effort.

Privacy: only counts are produced.  No prompt text, generated text, token
identifiers, or KV state ever leaves this module.
"""

from __future__ import annotations

import json
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

#: Cost status labels.
COST_STATUS_EXACT = "exact"
COST_STATUS_ESTIMATED = "estimated"
COST_STATUS_UNKNOWN = "unknown"

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
    #: True when the multimodal count is exact.  With no media present there
    #: is nothing to be inexact about, so this is True; a declared per-unit
    #: *estimate* prices the media but is not a measurement, so it is False.
    multimodal_is_exact: bool = False
    #: True when the output reservation is an upper bound on what the
    #: upstream may generate (the client stated a limit, or the broker
    #: forwards one).
    output_is_exact: bool = False
    #: True when the request carries content whose cost could **not** be
    #: determined.  This is distinct from "the cost is zero": a request
    #: with images and no per-unit estimate is *undercounted*, not free.
    #: When set, any budget comparison against this cost is best-effort.
    has_unknown_cost: bool = False

    #: Which request field supplied the effective output limit.
    #: ``max_tokens``, ``max_completion_tokens``, or ``default``.
    output_limit_source: str = "default"
    #: Tokens attributed to forwarded prompt-affecting fields (tool
    #: definitions, assistant tool calls, structured-output schemas).
    structured_prompt_tokens: int = 0
    #: Number of multimodal content parts seen in the request.
    media_parts: int = 0

    @property
    def cost_status(self) -> str:
        """``exact``, ``estimated``, or ``unknown`` — see module docstring."""
        if self.has_unknown_cost:
            return COST_STATUS_UNKNOWN
        if (
            self.input_is_exact
            and self.output_is_exact
            and self.multimodal_is_exact
        ):
            return COST_STATUS_EXACT
        return COST_STATUS_ESTIMATED

    def safe_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "multimodal_tokens": self.multimodal_tokens,
            "total_tokens": self.total_tokens,
            "input_is_exact": self.input_is_exact,
            "multimodal_is_exact": self.multimodal_is_exact,
            "output_is_exact": self.output_is_exact,
            "has_unknown_cost": self.has_unknown_cost,
            "output_limit_source": self.output_limit_source,
            "structured_prompt_tokens": self.structured_prompt_tokens,
            "media_parts": self.media_parts,
            "cost_status": self.cost_status,
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
    #: True when the comparison against the budgets is exact.  False when
    #: the cost estimate is inexact — most importantly when the request
    #: carries content whose cost could not be determined, in which case
    #: the hard-limit check is **best-effort**, not a guarantee.
    cost_is_exact: bool = True
    #: Tri-state cost semantics: ``exact``, ``estimated``, or ``unknown``.
    #: Always populated, including on the no-budget path, so that no
    #: consumer has to infer precision from a missing field.
    cost_status: str = COST_STATUS_EXACT
    #: Declared per-direction maxima, reported for observability.
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None

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
            "cost_is_exact": self.cost_is_exact,
            "cost_status": self.cost_status,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
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


def _safe_json(value: Any) -> str:
    """Serialize *value* to compact JSON for size estimation.

    Never raises: an unserializable value degrades to ``str()``.  The
    result is only used to *size* a payload, never to forward it.
    """
    if value is None:
        return ""
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _structured_prompt_text(request: Any) -> str:
    """Collect every prompt-affecting field the broker forwards upstream.

    ``build_forward_body()`` forwards tool definitions, assistant tool
    calls, structured-output schemas, tool choice, and stop sequences in
    addition to message content.  All of them end up in the assembled
    prompt, so all of them must be counted.  Counting only
    ``message.content`` undercounts exactly the requests that carry the
    largest hidden prompt.

    The serialization is a *size* proxy for the rendered prompt, not the
    rendered prompt itself — the backend renders tool schemas in its own
    template.  It is therefore an estimate, and the caller labels it so.
    """
    chunks: list[str] = []

    tools = getattr(request, "tools", None)
    if tools:
        chunks.append(_safe_json(tools))

    tool_choice = getattr(request, "tool_choice", None)
    if tool_choice:
        chunks.append(_safe_json(tool_choice))

    response_format = getattr(request, "response_format", None)
    if response_format:
        chunks.append(_safe_json(response_format))

    stop = getattr(request, "stop", None)
    if stop:
        chunks.append(_safe_json(stop))

    for message in getattr(request, "messages", None) or []:
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            chunks.append(_safe_json(tool_calls))
        name = getattr(message, "name", None)
        if name:
            chunks.append(str(name))
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            chunks.append(str(tool_call_id))

    return "\n".join(chunk for chunk in chunks if chunk)


def _count_tokens(
    text: str,
    token_counter: Optional[TokenCounter],
) -> tuple[int, bool]:
    """Count *text*, returning ``(tokens, is_exact)``."""
    if token_counter is not None:
        try:
            return max(0, int(token_counter(text))), True
        except Exception:
            pass
    return math.ceil(len(text) / CHARS_PER_TOKEN_FALLBACK), False


def _effective_output_limit(
    request: Any,
    default_max_tokens: int,
) -> tuple[int, bool, str]:
    """Resolve the output reservation for *request*.

    Returns ``(tokens, is_exact, source)``.

    ``max_completion_tokens`` is an alias for ``max_tokens``.  When a client
    supplies only ``max_completion_tokens``, the request model still carries
    the *default* ``max_tokens`` value, and the broker forwards both — so
    reserving only the default under-reserves by the difference.  Because
    the upstream's precedence between the two fields is not knowable from
    here, the **larger** of the two stated limits is reserved: the
    reservation is then an upper bound on what the upstream may generate.

    When neither field is stated, the broker's documented default is
    reserved and the figure is *not* exact — the upstream default is
    unknown.
    """
    max_tokens = getattr(request, "max_tokens", None)
    max_completion = getattr(request, "max_completion_tokens", None)

    if max_completion is not None:
        try:
            completion = max(0, int(max_completion))
        except (TypeError, ValueError):
            completion = 0
        other = 0
        if max_tokens is not None:
            try:
                other = max(0, int(max_tokens))
            except (TypeError, ValueError):
                other = 0
        return max(completion, other), True, "max_completion_tokens"

    if max_tokens is not None:
        try:
            return max(0, int(max_tokens)), True, "max_tokens"
        except (TypeError, ValueError):
            pass

    return max(0, int(default_max_tokens)), False, "default"


def estimate_request_cost(
    request: Any,
    *,
    token_counter: Optional[TokenCounter] = None,
    multimodal_token_estimate: Optional[int] = None,
    default_max_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
) -> RequestCost:
    """Estimate the token cost of a chat request.

    ``token_counter`` is an optional callable that returns the token count for
    a string.  When supplied, the input count is exact.  Otherwise a
    documented character heuristic is used and the result is flagged as
    inexact.

    The model's declared per-direction maxima are **not** applied here.  A
    declared ``max_output_tokens`` is a limit the request must be checked
    against, not a ceiling to silently clamp to — clamping would quietly
    rewrite the client's request.  Enforcement lives in
    :func:`evaluate_context`.
    """
    messages = getattr(request, "messages", None)
    text, _text_parts, media_parts = _message_text_and_media(messages)
    structured_text = _structured_prompt_text(request)

    text_tokens, text_is_exact = _count_tokens(text, token_counter)
    if structured_text:
        structured_tokens, structured_is_exact = _count_tokens(
            structured_text, token_counter
        )
    else:
        structured_tokens, structured_is_exact = 0, True
    input_tokens = text_tokens + structured_tokens
    input_is_exact = text_is_exact and structured_is_exact

    output_tokens, output_is_exact, output_source = _effective_output_limit(
        request, default_max_tokens
    )

    multimodal_is_exact = not bool(media_parts)
    has_unknown_cost = False
    if media_parts and multimodal_token_estimate:
        multimodal_tokens = media_parts * int(multimodal_token_estimate)
        # A declared per-unit estimate prices the media but is not a
        # backend-measured token count.
    else:
        multimodal_tokens = 0
        # Content without a declared estimate is undercounted, not free.
        has_unknown_cost = bool(media_parts)

    return RequestCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        multimodal_tokens=multimodal_tokens,
        total_tokens=input_tokens + output_tokens + multimodal_tokens,
        input_is_exact=input_is_exact,
        multimodal_is_exact=multimodal_is_exact,
        output_is_exact=output_is_exact,
        has_unknown_cost=has_unknown_cost,
        output_limit_source=output_source,
        structured_prompt_tokens=structured_tokens,
        media_parts=media_parts,
    )


def estimate_prompt_cost(
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    token_counter: Optional[TokenCounter] = None,
    default_max_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
) -> RequestCost:
    """Estimate the token cost of a bare prompt (the ``/v1/generate`` shape).

    ``/v1/generate`` carries a single prompt string and a ``max_tokens``
    limit — no messages, no tools, no structured output.
    """
    text = prompt or ""
    input_tokens, input_is_exact = _count_tokens(text, token_counter)

    if max_tokens is None:
        output_tokens = max(0, int(default_max_tokens))
        output_is_exact = False
        output_source = "default"
    else:
        try:
            output_tokens = max(0, int(max_tokens))
        except (TypeError, ValueError):
            output_tokens = max(0, int(default_max_tokens))
            output_is_exact = False
            output_source = "default"
        else:
            output_is_exact = True
            output_source = "max_tokens"

    return RequestCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        multimodal_tokens=0,
        total_tokens=input_tokens + output_tokens,
        input_is_exact=input_is_exact,
        multimodal_is_exact=True,
        output_is_exact=output_is_exact,
        has_unknown_cost=False,
        output_limit_source=output_source,
        structured_prompt_tokens=0,
        media_parts=0,
    )


# ── Budget evaluation ──────────────────────────────────────────────────────


def evaluate_context(
    cost: RequestCost, budgets: Optional[ContextBudgets]
) -> ContextEvaluation:
    """Compare a request cost against per-model context budgets.

    With no budgets the request is admitted unchanged and the reason is
    ``no_budget`` — an unmeasured model is not silently constrained.

    Exceeding ``hard_max_tokens`` is a rejection.  Exceeding
    ``recommended_max_tokens`` is admitted but records an advisory reason.
    No request is altered by exceeding it — the reason is reported, not
    executed.  Only the hard maximum rejects.

    The declared per-direction maxima (``max_input_tokens``,
    ``max_output_tokens``) are enforced independently of the combined
    totals.  A request may sit under the combined hard ceiling and still
    exceed what the model can accept on one side, so each direction is
    compared against its own declared limit and a breach is a rejection.
    """
    status = cost.cost_status
    exact = status == COST_STATUS_EXACT

    if budgets is None:
        return ContextEvaluation(
            reason=ContextDecisionReason.NO_BUDGET,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            cost_is_exact=exact,
            cost_status=status,
            cost=cost,
        )

    hard = budgets.hard_max_tokens
    recommended = budgets.recommended_max_tokens
    interactive = budgets.interactive_max_tokens
    max_input = budgets.max_input_tokens
    max_output = budgets.max_output_tokens

    common = {
        "hard_max_tokens": hard,
        "recommended_max_tokens": recommended,
        "interactive_max_tokens": interactive,
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
        "cost_is_exact": exact,
        "cost_status": status,
        "cost": cost,
    }

    # ── Per-direction maxima ─────────────────────────────────────────
    # Checked before the combined totals so the reported reason names the
    # actual breach.  A request that asks for more output than the model
    # can generate is refused rather than silently clamped.
    if max_output is not None and cost.output_tokens > max_output:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_OUTPUT_LIMIT,
            budget=ContextBudget.MAX_OUTPUT,
            total_tokens=cost.total_tokens,
            within_budget=False,
            over_limit=True,
            breached_budget=ContextBudget.MAX_OUTPUT,
            **common,
        )

    if max_input is not None and cost.input_tokens > max_input:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_INPUT_LIMIT,
            budget=ContextBudget.MAX_INPUT,
            total_tokens=cost.total_tokens,
            within_budget=False,
            over_limit=True,
            breached_budget=ContextBudget.MAX_INPUT,
            **common,
        )

    if hard is not None and cost.total_tokens > hard:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_HARD,
            budget=ContextBudget.HARD,
            total_tokens=cost.total_tokens,
            within_budget=False,
            over_limit=True,
            breached_budget=ContextBudget.HARD,
            **common,
        )

    if recommended is not None and cost.total_tokens > recommended:
        return ContextEvaluation(
            reason=ContextDecisionReason.ABOVE_RECOMMENDED,
            budget=ContextBudget.RECOMMENDED,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            **common,
        )

    if interactive is not None and cost.total_tokens > interactive:
        return ContextEvaluation(
            reason=ContextDecisionReason.WITHIN_RECOMMENDED,
            budget=ContextBudget.RECOMMENDED,
            total_tokens=cost.total_tokens,
            within_budget=True,
            over_limit=False,
            **common,
        )

    return ContextEvaluation(
        reason=ContextDecisionReason.WITHIN_INTERACTIVE,
        budget=ContextBudget.INTERACTIVE,
        total_tokens=cost.total_tokens,
        within_budget=True,
        over_limit=False,
        **common,
    )

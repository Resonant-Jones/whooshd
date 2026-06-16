"""Prompt canonicalisation and segmentation for ThreadWake observe mode."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .keys import canonical_json, canonicalize_content, hash_json, sha256_hex
from .metadata import parse_codexify_segments, validate_and_merge_segments
from .types import PromptGraph, PromptSegment, ThreadWakeScope


DEFAULT_CHAT_TEMPLATE_HASH = sha256_hex("openai-chat-completions-v1")
_TOKEN_RE = re.compile(r"\S+")


def contains_multimodal_content(content: Any) -> bool:
    """Detect multimodal OpenAI content arrays without deeply caching them."""

    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type and part_type != "text":
            return True
        if "image_url" in part:
            return True
    return False


def estimate_token_count(canonical_payload: str) -> int:
    """Return a deterministic approximate token count.

    ThreadWake Phase A should not depend on a specific backend tokenizer. This
    estimate is intentionally conservative and deterministic for policy gates.
    """

    if not canonical_payload:
        return 0
    return len(_TOKEN_RE.findall(canonical_payload))


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content", "")
    return getattr(message, "content", "")


def _message_payload(message: Any) -> dict[str, Any]:
    """Build a deterministic semantic payload for a chat message."""

    role = _message_role(message)
    payload: dict[str, Any] = {
        "role": role,
        "content": _message_content(message),
    }
    for field in ("name", "tool_calls", "tool_call_id"):
        if isinstance(message, dict):
            value = message.get(field)
        else:
            value = getattr(message, field, None)
        if value is not None:
            payload[field] = value
    return payload


def _latest_user_index(messages: Iterable[Any]) -> int | None:
    latest: int | None = None
    for idx, message in enumerate(messages):
        if _message_role(message) == "user":
            latest = idx
    return latest


def _message_stability(message: Any, idx: int, latest_user_idx: int | None) -> str:
    role = _message_role(message)
    if role in {"system", "developer"}:
        return "stable"
    if role == "tool":
        return "dynamic"
    if role == "user" and idx == latest_user_idx:
        return "dynamic"
    if role in {"user", "assistant"}:
        return "semi_stable"
    return "dynamic"


def _segment_hash_payload(segment: PromptSegment) -> dict[str, Any]:
    return {
        "name": segment.name,
        "role": segment.role,
        "content_hash": segment.content_hash,
        "segment_type": segment.segment_type,
        "stability": segment.stability,
        "token_count": segment.token_count,
        "scope": segment.scope,
        "multimodal": segment.multimodal,
    }


def compile_prompt_graph(
    *,
    messages: list[Any],
    model_id: str | None,
    backend: str | None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    tokenizer_hash: str | None = None,
    chat_template_hash: str | None = None,
    scope: ThreadWakeScope = "thread",
    codexify_segments: Any = None,
    allow_global: bool = False,
) -> PromptGraph:
    """Compile chat request inputs into a deterministic prompt graph.

    If ``codexify_segments`` is provided, it is validated against the
    actual message content and merged into the inferred segments.
    """

    segments: list[PromptSegment] = []

    if tools is not None:
        canonical = canonicalize_content({"tools": tools})
        segments.append(
            PromptSegment(
                name="tools",
                role="tool_schema",
                content_hash=sha256_hex(canonical),
                segment_type="tool_schema",
                stability="stable",
                token_count=estimate_token_count(canonical),
                scope=scope,
            )
        )

    if tool_choice is not None:
        canonical = canonicalize_content({"tool_choice": tool_choice})
        segments.append(
            PromptSegment(
                name="tool_choice",
                role="tool_instruction",
                content_hash=sha256_hex(canonical),
                segment_type="tool_instruction",
                stability="stable",
                token_count=estimate_token_count(canonical),
                scope=scope,
            )
        )

    latest_user_idx = _latest_user_index(messages)
    for idx, message in enumerate(messages):
        role = _message_role(message)
        payload = _message_payload(message)
        canonical = canonicalize_content(payload)
        multimodal = contains_multimodal_content(_message_content(message))
        segment_type = "multimodal_message" if multimodal else "message"
        segments.append(
            PromptSegment(
                name=f"message:{idx}:{role}",
                role=role,
                content_hash=sha256_hex(canonical),
                segment_type=segment_type,
                stability=_message_stability(message, idx, latest_user_idx),
                token_count=estimate_token_count(canonical),
                scope=scope,
                multimodal=multimodal,
            )
        )

    # ── Apply Codexify segment metadata (Phase F) ──────────────────────
    codexify_meta = parse_codexify_segments(codexify_segments)
    if codexify_meta is not None and codexify_meta.segments:
        segments, _errors = validate_and_merge_segments(
            inferred_segments=segments,
            codexify_metadata=codexify_meta,
            messages=messages,
            default_scope=scope,
            allow_global=allow_global,
        )
        # Errors are logged but don't block — invalid entries fall back to inferred

    stable_prefix_tokens = 0
    stable_payload: list[dict[str, Any]] = []
    prefix_open = True
    for segment in segments:
        if prefix_open and segment.stability in {"stable", "semi_stable"}:
            segment.in_stable_prefix = True
            stable_prefix_tokens += segment.token_count
            stable_payload.append(_segment_hash_payload(segment))
            continue
        prefix_open = False

    full_payload = [_segment_hash_payload(segment) for segment in segments]
    total_tokens = sum(segment.token_count for segment in segments)

    # ── Session continuation (Phase E) ──────────────────────────────────
    ordered_hashes = [seg.content_hash for seg in segments]
    chain = ""
    for h in ordered_hashes:
        chain = sha256_hex(chain + h)
    chain_hash = chain
    continuation_candidate = len(segments) > 0

    return PromptGraph(
        model_id=model_id,
        backend=backend,
        chat_template_hash=chat_template_hash or DEFAULT_CHAT_TEMPLATE_HASH,
        tokenizer_hash=tokenizer_hash,
        segments=segments,
        stable_prefix_hash=hash_json(stable_payload),
        full_prompt_hash=hash_json(full_payload),
        stable_prefix_tokens=stable_prefix_tokens,
        dynamic_tokens=max(0, total_tokens - stable_prefix_tokens),
        ordered_segment_hashes=ordered_hashes,
        full_prefix_chain_hash=chain_hash,
        continuation_candidate=continuation_candidate,
    )

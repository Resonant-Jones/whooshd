"""Shared MLX prompt rendering — used by both inference and ThreadWake.

This module provides the single source of truth for converting
OpenAI-compatible chat messages into a model-ready prompt string.

Both the MLX inference adapter and the ThreadWake MLX tokenizer
adapter MUST use this module so that KV cache reuse operates on
the exact same rendered prompt.
"""

from __future__ import annotations

from typing import Any


def extract_chat_messages(request: Any) -> list[dict[str, str]]:
    """Extract role+content dicts from an OpenAI-compatible request.

    Accepts both Pydantic model requests (with ``.role`` / ``.content``
    attributes) and plain dict-style messages.
    """
    messages = getattr(request, "messages", [])
    result: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, dict):
            result.append({
                "role": str(m.get("role", "user")),
                "content": str(m.get("content", "")),
            })
        else:
            result.append({
                "role": str(getattr(m, "role", "user")),
                "content": str(getattr(m, "content", "")),
            })
    return result


def render_mlx_chat_prompt(
    tokenizer: Any,
    messages: list[dict[str, str]],
) -> str:
    """Render chat messages into a prompt string for MLX inference.

    Uses ``tokenizer.apply_chat_template`` when available with
    ``add_generation_prompt=True, tokenize=False``.  Falls back to
    a simple role-annotated transcript otherwise.

    This is the single rendering function used by both the MLX
    inference adapter and the ThreadWake MLX tokenizer adapter.
    """
    if tokenizer is not None and hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=False,
        )

    # Fallback transcript for tokenizers without a chat template.
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(f"User: {content}")
    parts.append("Assistant: ")
    return "\n".join(parts)

"""MLX in-process tokenizer adapter for ThreadWake Phase M3.

Provides real token IDs by using the actual MLX tokenizer object
and the exact same prompt rendering path used by inference.

Capability: ``token_ids`` — full prompt tokenization with conservative
stable/dynamic split via incremental rendering.  Segment-level spans
(``token_ids_with_spans``) are not yet implemented — chat template
special-token interleaving makes per-message span mapping non-trivial.

This adapter requires the real MLX tokenizer.  When MLX dependencies
are unavailable, it degrades cleanly by returning
``real_tokenization=False``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from .tokenization import (
    ThreadWakeTokenizerCapability,
    TokenizedPrompt,
    TokenSpan,
)

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────


def _tokenizer_hash(tokenizer: Any) -> str | None:
    """Derive a deterministic tokenizer identity hash.

    Uses the tokenizer's ``name_or_path`` and vocabulary size if
    available.  Falls back to None if the tokenizer object doesn't
    expose these attributes.
    """
    try:
        name = getattr(tokenizer, "name_or_path", "unknown")
        vocab_size = getattr(tokenizer, "vocab_size", 0)
        payload = f"{name}:{vocab_size}"
        return hashlib.sha256(payload.encode()).hexdigest()
    except Exception:
        return None


def _chat_template_hash(tokenizer: Any) -> str | None:
    """Derive a deterministic chat template hash."""
    try:
        template = getattr(tokenizer, "chat_template", None)
        if template is None:
            return None
        if isinstance(template, str):
            return hashlib.sha256(template.encode()).hexdigest()
        # Jinja2 template object — hash its source
        source = getattr(template, "source", str(template))
        return hashlib.sha256(str(source).encode()).hexdigest()
    except Exception:
        return None


def _render_prompt(tokenizer: Any, messages: list[dict]) -> str | None:
    """Render messages into a prompt string using the chat template.

    Returns None if rendering fails.
    """
    try:
        if hasattr(tokenizer, "apply_chat_template"):
            return tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=False,
            )
        # Fallback: simple transcript
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
    except Exception:
        return None


def _tokenize(tokenizer: Any, text: str) -> list[int] | None:
    """Tokenize a text string.  Returns None on failure."""
    try:
        if hasattr(tokenizer, "encode"):
            result = tokenizer.encode(text)
            if isinstance(result, list):
                return [int(t) for t in result]
            # HuggingFace tokenizers may return an Encoding object
            if hasattr(result, "ids"):
                return [int(t) for t in result.ids]
        return None
    except Exception:
        return None


def _extract_messages(request: Any) -> list[dict]:
    """Extract message dicts from a request object."""
    messages = getattr(request, "messages", [])
    result: list[dict] = []
    for m in messages:
        if isinstance(m, dict):
            result.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        else:
            result.append({
                "role": getattr(m, "role", "user"),
                "content": getattr(m, "content", ""),
            })
    return result


# ── Adapter ────────────────────────────────────────────────────────────────


class MLXInProcessTokenizerAdapter:
    """Real MLX tokenizer adapter for ThreadWake.

    Requires an MLX tokenizer object (from ``mlx_lm.load()``).
    Produces ``token_ids`` capability — full prompt tokenization
    with incremental rendering for stable/dynamic split.

    Segment-level spans (``token_ids_with_spans``) are deferred
    to a future phase due to chat-template special-token interleaving.
    """

    def __init__(self, tokenizer: Any = None) -> None:
        self._tokenizer = tokenizer
        self._has_tokenizer = tokenizer is not None

    # ── Capability ────────────────────────────────────────────────────

    def supports_tokenization(self) -> ThreadWakeTokenizerCapability:
        if not self._has_tokenizer:
            return ThreadWakeTokenizerCapability.ESTIMATES_ONLY
        return ThreadWakeTokenizerCapability.TOKEN_IDS

    # ── Tokenization ───────────────────────────────────────────────────

    def tokenize_prompt(
        self,
        graph: Any,
        request: Any,
        *,
        model_id: str,
    ) -> TokenizedPrompt:
        if not self._has_tokenizer or self._tokenizer is None:
            return TokenizedPrompt(
                model_id=model_id,
                real_tokenization=False,
                unavailable_reason="mlx_tokenizer_not_available",
            )

        tok_hash = _tokenizer_hash(self._tokenizer)
        tmpl_hash = _chat_template_hash(self._tokenizer)

        try:
            messages = _extract_messages(request)
            if not messages:
                return TokenizedPrompt(
                    model_id=model_id,
                    backend="mlx",
                    tokenizer_hash=tok_hash,
                    chat_template_hash=tmpl_hash,
                    real_tokenization=False,
                    unavailable_reason="no_messages_to_tokenize",
                )

            # Render the full prompt
            full_prompt = _render_prompt(self._tokenizer, messages)
            if full_prompt is None:
                return TokenizedPrompt(
                    model_id=model_id,
                    backend="mlx",
                    tokenizer_hash=tok_hash,
                    chat_template_hash=tmpl_hash,
                    real_tokenization=False,
                    unavailable_reason="prompt_rendering_failed",
                )

            # Tokenize the full prompt
            all_ids = _tokenize(self._tokenizer, full_prompt)
            if all_ids is None:
                return TokenizedPrompt(
                    model_id=model_id,
                    backend="mlx",
                    tokenizer_hash=tok_hash,
                    chat_template_hash=tmpl_hash,
                    real_tokenization=False,
                    unavailable_reason="tokenization_failed",
                )

            # Incremental rendering: stable-prefix messages only
            stable_messages = [
                messages[i] for i, seg in enumerate(graph.segments)
                if seg.in_stable_prefix and i < len(messages)
            ]
            stable_prefix_ids: list[int] = []
            dynamic_tail_ids: list[int] = list(all_ids)

            if stable_messages and len(stable_messages) < len(messages):
                stable_prompt = _render_prompt(self._tokenizer, stable_messages)
                if stable_prompt is not None:
                    stable_ids = _tokenize(self._tokenizer, stable_prompt)
                    if stable_ids is not None and len(stable_ids) <= len(all_ids):
                        stable_prefix_ids = stable_ids
                        dynamic_tail_ids = all_ids[len(stable_ids):]

            # Build minimal span list for observability
            spans: list[TokenSpan] = []
            for i, seg in enumerate(graph.segments):
                spans.append(TokenSpan(
                    segment_name=seg.name,
                    segment_index=i,
                    token_count=seg.token_count,
                    segment_type=seg.segment_type,
                    stability=seg.stability,
                    scope=seg.scope,
                    content_hash=seg.content_hash,
                ))

            return TokenizedPrompt(
                model_id=model_id,
                backend="mlx",
                tokenizer_hash=tok_hash,
                chat_template_hash=tmpl_hash,
                token_ids=all_ids,
                stable_prefix_token_ids=stable_prefix_ids,
                dynamic_tail_token_ids=dynamic_tail_ids,
                spans=spans,
                stable_prefix_token_count=len(stable_prefix_ids),
                dynamic_tail_token_count=len(dynamic_tail_ids),
                real_tokenization=True,
            )

        except Exception as exc:
            logger.warning("MLX tokenizer adapter failed: %s", exc)
            return TokenizedPrompt(
                model_id=model_id,
                backend="mlx",
                tokenizer_hash=tok_hash,
                chat_template_hash=tmpl_hash,
                real_tokenization=False,
                unavailable_reason=f"mlx_tokenizer_error: {exc}",
            )

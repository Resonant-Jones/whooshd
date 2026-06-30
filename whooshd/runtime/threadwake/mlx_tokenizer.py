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
from whooshd.adapters.mlx_prompt import extract_chat_messages, render_mlx_chat_prompt

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
            # Use the shared message extraction + prompt rendering.
            messages = extract_chat_messages(request)
            if not messages:
                return TokenizedPrompt(
                    model_id=model_id,
                    backend="mlx",
                    tokenizer_hash=tok_hash,
                    chat_template_hash=tmpl_hash,
                    real_tokenization=False,
                    unavailable_reason="no_messages_to_tokenize",
                )

            # Render full prompt using shared renderer.
            full_prompt = render_mlx_chat_prompt(self._tokenizer, messages)

            # Tokenize the full prompt.
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

            # ── Conservative stable-prefix split ──────────────────────
            stable_prefix_ids: list[int] = []
            dynamic_tail_ids: list[int] = list(all_ids)

            stable_messages = [
                messages[i] for i, seg in enumerate(graph.segments)
                if seg.in_stable_prefix and i < len(messages)
            ]

            if stable_messages and len(stable_messages) < len(messages):
                stable_prompt = render_mlx_chat_prompt(
                    self._tokenizer, stable_messages
                )
                stable_ids = _tokenize(self._tokenizer, stable_prompt)

                # Only accept stable split if the stable-prefix token IDs
                # are a true leading prefix of the full prompt token IDs.
                if (
                    stable_ids is not None
                    and len(stable_ids) <= len(all_ids)
                    and all_ids[:len(stable_ids)] == stable_ids
                ):
                    stable_prefix_ids = stable_ids
                    dynamic_tail_ids = all_ids[len(stable_ids):]
                # else: non-prefix stable render — leave stable_prefix_ids empty;
                #        do not claim reusable prefix tokens.

            # Build minimal span list for observability.
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

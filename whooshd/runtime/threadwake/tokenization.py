"""Backend-owned tokenization boundary for ThreadWake Phase M1.

Defines the contract that backend adapters implement to provide real
token IDs and segment token spans.  ThreadWake uses these spans to
split prompts into stable-prefix and dynamic-tail token ID lists for
KV cache reuse.

Production backends report ``unsupported`` and degrade safely.
A ``FakeTokenizerAdapter`` is provided for test/benchmark flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class ThreadWakeTokenizerCapability(str, Enum):
    """What level of tokenization the backend can provide."""

    UNSUPPORTED = "unsupported"
    ESTIMATES_ONLY = "estimates_only"
    TOKEN_IDS = "token_ids"
    TOKEN_IDS_WITH_SPANS = "token_ids_with_spans"


# ── Data models ─────────────────────────────────────────────────────────────


@dataclass
class TokenSpan:
    """A single segment's token span within the full prompt tokenization."""

    segment_name: str
    segment_index: int = 0
    start: int = 0        # inclusive start token index
    end: int = 0          # exclusive end token index
    token_count: int = 0
    segment_type: str = ""
    stability: str = ""
    scope: str = ""
    content_hash: str = ""


@dataclass
class TokenizedPrompt:
    """Full tokenization of a prompt with stable/dynamic split.

    ``real_tokenization`` is True only when a backend tokenizer
    produced the token IDs.  If ``real_tokenization`` is False,
    ``token_ids`` is empty and callers must use estimate fields.
    """

    model_id: str = ""
    backend: str = ""
    tokenizer_hash: str | None = None
    chat_template_hash: str | None = None
    token_ids: list[int] = field(default_factory=list)
    stable_prefix_token_ids: list[int] = field(default_factory=list)
    dynamic_tail_token_ids: list[int] = field(default_factory=list)
    spans: list[TokenSpan] = field(default_factory=list)
    stable_prefix_token_count: int = 0
    dynamic_tail_token_count: int = 0
    real_tokenization: bool = False
    unavailable_reason: str | None = None


# ── Protocol ────────────────────────────────────────────────────────────────


class BackendTokenizerAdapter(Protocol):
    """Protocol for backends that can provide tokenization.

    Separate from ``KVCapableBackend`` by design — a backend may
    support tokenization without supporting KV reuse, and vice versa.
    """

    def supports_tokenization(self) -> ThreadWakeTokenizerCapability:
        """Return the tokenization capability level.

        Must never raise; return ``UNSUPPORTED`` if tokenization
        is not available.
        """
        ...

    def tokenize_prompt(
        self,
        graph: Any,       # PromptGraph
        request: Any,     # ChatCompletionRequest
        *,
        model_id: str,
    ) -> TokenizedPrompt:
        """Tokenize the request into a TokenizedPrompt.

        The adapter is responsible for:
        - Rendering the chat template
        - Running the tokenizer
        - Computing per-segment token spans
        - Splitting stable prefix vs. dynamic tail token IDs

        Returns a ``TokenizedPrompt`` with ``real_tokenization=True``
        on success, or ``real_tokenization=False`` with a reason
        on failure.
        """
        ...


# ── No-op adapter ───────────────────────────────────────────────────────────


class NoOpTokenizerAdapter:
    """Safe no-op implementation for backends without tokenization.

    Always reports ``UNSUPPORTED`` and returns empty tokenized prompts.
    """

    def supports_tokenization(self) -> ThreadWakeTokenizerCapability:
        return ThreadWakeTokenizerCapability.UNSUPPORTED

    def tokenize_prompt(
        self,
        graph: Any,
        request: Any,
        *,
        model_id: str,
    ) -> TokenizedPrompt:
        return TokenizedPrompt(
            model_id=model_id,
            real_tokenization=False,
            unavailable_reason="backend_tokenizer_unsupported",
        )


# ── Fake adapter (tests / benchmarks) ──────────────────────────────────────


class FakeTokenizerAdapter:
    """Deterministic tokenizer adapter for tests and benchmarks.

    Produces synthetic integer token IDs derived from segment content
    hashes and produces aligned TokenSpans.  Not for production use.
    """

    def supports_tokenization(self) -> ThreadWakeTokenizerCapability:
        return ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS

    def tokenize_prompt(
        self,
        graph: Any,
        request: Any,
        *,
        model_id: str,
    ) -> TokenizedPrompt:
        # Use content hash bytes as deterministic "token IDs"
        all_ids: list[int] = []
        spans: list[TokenSpan] = []
        cursor = 0

        for i, seg in enumerate(graph.segments):
            h = seg.content_hash
            # Generate a deterministic token-id sequence from the hash
            seg_ids: list[int] = []
            for j in range(min(seg.token_count, 64)):
                # Use pairs of hex chars as uint16 token IDs
                offset = (j * 4) % len(h)
                chunk = h[offset:offset + 4] or "0000"
                seg_ids.append(int(chunk, 16) % 65536)

            if not seg_ids:
                seg_ids = [0]

            start = cursor
            end = cursor + len(seg_ids)
            spans.append(TokenSpan(
                segment_name=seg.name,
                segment_index=i,
                start=start,
                end=end,
                token_count=len(seg_ids),
                segment_type=seg.segment_type,
                stability=seg.stability,
                scope=seg.scope,
                content_hash=h,
            ))
            all_ids.extend(seg_ids)
            cursor = end

        # Split into stable prefix and dynamic tail
        stable_ids: list[int] = []
        dynamic_ids: list[int] = []
        stable_count = 0
        for i, seg in enumerate(graph.segments):
            span = spans[i]
            seg_ids = all_ids[span.start:span.end]
            if seg.in_stable_prefix:
                stable_ids.extend(seg_ids)
                stable_count += len(seg_ids)
            else:
                dynamic_ids.extend(seg_ids)

        return TokenizedPrompt(
            model_id=model_id,
            backend=graph.backend or "",
            token_ids=all_ids,
            stable_prefix_token_ids=stable_ids,
            dynamic_tail_token_ids=dynamic_ids,
            spans=spans,
            stable_prefix_token_count=stable_count,
            dynamic_tail_token_count=len(dynamic_ids),
            real_tokenization=True,
        )


# ── Registry ────────────────────────────────────────────────────────────────


class BackendTokenizerAdapterRegistry:
    """Registry mapping backend names to tokenizer adapter instances.

    Unregistered backends receive the no-op adapter automatically.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, BackendTokenizerAdapter] = {}
        self._noop = NoOpTokenizerAdapter()

    def register(self, backend: str, adapter: BackendTokenizerAdapter) -> None:
        self._adapters[backend] = adapter
        logger.debug(
            "BackendTokenizerAdapterRegistry: registered %s -> %s",
            backend, type(adapter).__name__,
        )

    def get(self, backend: str) -> BackendTokenizerAdapter:
        return self._adapters.get(backend, self._noop)

    def capability(self, backend: str) -> ThreadWakeTokenizerCapability:
        return self.get(backend).supports_tokenization()

    def has_real_tokenization(self, backend: str) -> bool:
        cap = self.capability(backend)
        return cap in (
            ThreadWakeTokenizerCapability.TOKEN_IDS,
            ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS,
        )

    def registered_backends(self) -> list[str]:
        return sorted(self._adapters.keys())

    def clear(self) -> None:
        self._adapters.clear()

"""Inference adapter protocol.

All inference backends (stub, mlx-lm, etc.) implement this interface
so the HTTP layer stays thin and testable.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    GenerateRequest,
    GenerateResponse,
)


class StreamingNotSupportedError(Exception):
    """Raised when a request asks for streaming but the adapter cannot serve it."""

    def __init__(self, message: str = "Streaming is not supported by this adapter"):
        super().__init__(message)


class InferenceAdapter(Protocol):
    """Protocol for inference backends.

    Each adapter is responsible for accepting inference requests,
    running inference (or producing stub output), and returning
    typed responses.
    """

    @property
    def name(self) -> str:
        """Human-readable adapter identifier (e.g. 'stub', 'mlx-lm')."""
        ...

    @property
    def supports_streaming(self) -> bool:
        """Whether this adapter can serve streaming responses."""
        ...

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run inference for a single generation request (Codexify format)."""
        ...

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Run inference for an OpenAI-compatible chat completion request."""
        ...

    async def chat_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream inference chunks for an OpenAI-compatible chat completion request."""
        ...

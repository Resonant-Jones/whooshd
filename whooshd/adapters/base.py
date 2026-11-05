"""Inference adapter protocol.

All inference backends (stub, mlx-lm, mlx-lm-server, llama-cpp, etc.)
implement this interface so the HTTP layer stays thin and testable.
"""

from __future__ import annotations

from typing import Optional, AsyncIterator, Optional, Protocol

from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    GenerateRequest,
    GenerateResponse,
    RequestExecutionContext,
    RuntimeHealth,
    RuntimeModel,
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

    Multi-runtime support: every adapter exposes its ``kind``
    (e.g. ``"llama_cpp"``, ``"mlx_lm_server"``) so the router
    can dispatch requests to the correct backend.
    """

    @property
    def name(self) -> str:
        """Human-readable adapter identifier (e.g. 'stub', 'mlx-lm')."""
        ...

    @property
    def kind(self) -> str:
        """Runtime kind identifier for routing (e.g. 'llama_cpp', 'mlx_lm_server')."""
        ...

    @property
    def supports_streaming(self) -> bool:
        """Whether this adapter can serve streaming responses."""
        ...

    @property
    def max_concurrent_requests(self) -> Optional[int]:
        """Concurrency ceiling for this inference lane, or None.

        A lane that can only execute a bounded number of requests at once
        (for example the MLX-VLM adapter, whose semaphore defaults to one)
        declares that ceiling here.  Admission uses it so the broker does
        not admit a request that will then stall at the adapter's own
        semaphore and be refused.

        ``None`` means the lane declares no ceiling of its own, and the
        global operator ceiling applies unchanged.  The default
        implementation returns ``None`` so adapters that do not care need
        not implement it.
        """
        return None

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run inference for a single generation request (Codexify format)."""
        ...

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        context: RequestExecutionContext | None = None,
    ) -> ChatCompletionResponse:
        """Run inference for an OpenAI-compatible chat completion request."""
        ...

    async def chat_completion_stream(
        self,
        request: ChatCompletionRequest,
        context: RequestExecutionContext | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream inference chunks for an OpenAI-compatible chat completion request."""
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────

    def is_loaded(self) -> bool:
        """Return True if the adapter currently holds a loaded model."""
        ...

    def model_id(self) -> Optional[str]:
        """Return the loaded model identifier, or None."""
        ...

    async def warmup(self) -> None:
        """Ensure the model is loaded and ready for inference."""
        ...

    async def unload(self) -> None:
        """Release the model from memory (subject to active-request checks)."""
        ...

    # ── Multi-runtime introspection ──────────────────────────────────

    async def health(self) -> RuntimeHealth:
        """Return the current health state of this runtime."""
        ...

    async def list_models(self) -> list[RuntimeModel]:
        """Return the list of models managed by this runtime."""
        ...

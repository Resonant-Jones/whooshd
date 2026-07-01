"""Stub inference adapter for testing.

Returns deterministic text so contract tests can run without a real model.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncIterator

from whooshd.config import get_stub_response_delay_seconds
from whooshd.contracts import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChoice,
    ChatCompletionDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    ResponseRuntimeInfo,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeKind,
    RuntimeModel,
    TokenUsage,
)

_STUB_CHAT_TEXT = "Whoosh'd stub response: chat completion contract is online."
_STUB_STREAM_TOKENS = ["Whoosh'd ", "streaming ", "stub ", "online."]


class StubInferenceAdapter:
    """Deterministic stub adapter for test suites.

    Every call returns a predictable response so test assertions
    can verify endpoint shape, validation, and routing without
    depending on MLX or downloaded models.
    """

    def __init__(self) -> None:
        self._call_count = 0
        self._chat_call_count = 0

    @property
    def name(self) -> str:
        return "stub"

    @property
    def kind(self) -> str:
        return RuntimeKind.STUB.value

    @property
    def supports_streaming(self) -> bool:
        return True

    # ── Batch execution ────────────────────────────────────────────────

    def supports_chat_batching(self) -> str:
        """Stub supports experimental chat batching for tests."""
        from whooshd.config import get_batch_execution_enabled
        if get_batch_execution_enabled():
            return "experimental"
        return "unsupported"

    async def chat_completion_batch(
        self,
        requests,
        contexts=None,
    ):
        """Execute a batch of chat completions — deterministic stub output."""
        results = []
        for i, req in enumerate(requests):
            ctx = contexts[i] if contexts and i < len(contexts) else None
            result = await self.chat_completion(req, context=ctx)
            results.append(result)
        return results

    # ── Codexify-style generate ────────────────────────────────────────

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        t0 = time.monotonic()

        self._call_count += 1

        request_id = request.request_id or str(uuid.uuid4())
        model_id = request.model_id or "stub-model"

        # Deterministic text: echoes the prompt so tests can assert shape.
        text = (
            f"[stub response #{self._call_count}] "
            f"echo: {request.prompt[:120]}"
        )

        elapsed_ms = (time.monotonic() - t0) * 1000.0

        return GenerateResponse(
            ok=True,
            request_id=request_id,
            model_id=model_id,
            text=text,
            finish_reason="stop",
            usage=TokenUsage(
                prompt_tokens=len(request.prompt.split()),
                completion_tokens=len(text.split()),
                total_tokens=len(request.prompt.split()) + len(text.split()),
            ),
            runtime=ResponseRuntimeInfo(
                adapter=self.name,
                queued=False,
                elapsed_ms=round(elapsed_ms, 3),
            ),
        )

    # ── OpenAI-compatible chat completions ─────────────────────────────

    async def chat_completion(self, request: ChatCompletionRequest, context=None) -> ChatCompletionResponse:
        """Non-streaming chat completion — returns the full response at once."""
        self._chat_call_count += 1

        t0 = time.monotonic()

        # Apply optional stub delay for live queue smoke tests.
        delay = get_stub_response_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        # Approximate prompt tokens from concatenated message content.
        prompt_text = " ".join(m.content for m in request.messages)
        prompt_tokens = max(1, len(prompt_text.split()))
        completion_tokens = len(_STUB_CHAT_TEXT.split())

        request_id = f"chatcmpl-stub-{uuid.uuid4().hex[:12]}"

        return ChatCompletionResponse(
            id=request_id,
            object="chat.completion",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=_STUB_CHAT_TEXT),
                    finish_reason="stop",
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    # ── OpenAI-compatible streaming chat ───────────────────────────────

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, context=None
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Streaming chat completion — yields one chunk per word token.

        Checks the cancellation token in *context* before each chunk and
        stops cleanly when cancellation is requested.
        """
        self._chat_call_count += 1

        request_id = f"chatcmpl-stub-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # Check cancellation before starting.
        if context and context.cancellation_token.is_cancelled():
            return

        # Apply optional stub delay for live queue smoke tests.
        delay = get_stub_response_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        # Yield control so the caller can observe the request is in-flight.
        await asyncio.sleep(0)

        # Chunk 1: assistant role marker, no content.
        yield ChatCompletionChunk(
            id=request_id,
            created=created,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionDelta(role="assistant"),
                )
            ],
        )

        # Chunks 2..N: one content delta per word token.
        for token in _STUB_STREAM_TOKENS:
            if context and context.cancellation_token.is_cancelled():
                return
            await asyncio.sleep(0)
            yield ChatCompletionChunk(
                id=request_id,
                created=created,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionDelta(content=token),
                    )
                ],
            )

        # Cancellation check before final chunk.
        if context and context.cancellation_token.is_cancelled():
            return

        # Final chunk: empty delta, finish_reason = stop.
        yield ChatCompletionChunk(
            id=request_id,
            created=created,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionDelta(),
                    finish_reason="stop",
                )
            ],
        )

    # ── Lifecycle (stub — always loaded) ───────────────────────────────

    def set_external_model_path(self, path: str) -> None:
        """Accept an external model path for runtime handoff.

        Stub adapter ignores external paths — it always returns stub output.
        """
        pass

    def is_loaded(self) -> bool:
        return True

    def model_id(self) -> Optional[str]:
        return "stub-model"

    async def warmup(self) -> None:
        """Instant no-op — stub is always ready."""
        pass

    async def unload(self) -> None:
        """No-op — stub does not hold resources."""
        pass

    # ── Multi-runtime introspection ──────────────────────────────────

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            kind=self.kind,
            enabled=True,
            state=RuntimeHealthState.READY,
            active_model="stub-model",
            detail="Stub adapter is always ready.",
        )

    async def list_models(self) -> list[RuntimeModel]:
        return [
            RuntimeModel(
                id="stub-model",
                display_name="Stub Model",
                runtime=self.kind,
                format="unknown",
                path=None,
                context_window=32768,
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                loaded=True,
                state=RuntimeHealthState.READY.value,
            )
        ]

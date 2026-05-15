"""Stub inference adapter for testing.

Returns deterministic text so contract tests can run without a real model.
"""

from __future__ import annotations

import time
import uuid

from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.contracts import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    ResponseRuntimeInfo,
    TokenUsage,
)

_STUB_CHAT_TEXT = "Whoosh'd stub response: chat completion contract is online."


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
    def supports_streaming(self) -> bool:
        return False

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

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        if request.stream:
            raise StreamingNotSupportedError(
                "Streaming is not available in stub mode. "
                "Set stream=false or use a real inference adapter."
            )

        self._chat_call_count += 1

        t0 = time.monotonic()

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

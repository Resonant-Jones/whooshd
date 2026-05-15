"""Stub inference adapter for testing.

Returns deterministic text so contract tests can run without a real model.
"""

from __future__ import annotations

import time
import uuid

from whooshd.contracts import (
    GenerateRequest,
    GenerateResponse,
    ResponseRuntimeInfo,
    TokenUsage,
)


class StubInferenceAdapter:
    """Deterministic stub adapter for test suites.

    Every call returns a predictable response so test assertions
    can verify endpoint shape, validation, and routing without
    depending on MLX or downloaded models.
    """

    def __init__(self) -> None:
        self._call_count = 0

    @property
    def name(self) -> str:
        return "stub"

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

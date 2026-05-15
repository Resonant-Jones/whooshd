"""MLX inference adapter.

Lazy-imports mlx-lm so the normal test suite never touches it.
Model loading is guarded by an asyncio lock so concurrent first requests
do not trigger duplicate loads.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from whooshd.adapters.base import StreamingNotSupportedError
from whooshd.config import (
    get_mlx_max_tokens_default,
    get_mlx_model_path,
)
from whooshd.contracts import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    ResponseRuntimeInfo,
    RunnerStatus,
    TokenUsage,
)


class MLXInferenceAdapter:
    """Inference adapter backed by mlx-lm.

    Model and tokenizer are loaded lazily on the first request.  Subsequent
    requests reuse the cached objects.  Streaming is not yet implemented
    (see Phase 1F).
    """

    def __init__(self) -> None:
        self._model: object = None
        self._tokenizer: object = None
        self._model_path: Optional[str] = None
        self._load_lock = asyncio.Lock()
        self._loaded = False

    # ── Protocol properties ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "mlx-lm"

    @property
    def supports_streaming(self) -> bool:
        return False

    # ── Lazy import ─────────────────────────────────────────────────────

    @staticmethod
    def _import_mlx_lm():
        """Import mlx_lm — only called when the MLX adapter is active."""
        import mlx_lm  # type: ignore[import-untyped]

        return mlx_lm

    # ── Model loading ───────────────────────────────────────────────────

    async def _ensure_loaded(self, model_path: str) -> None:
        """Load model + tokenizer, or no-op if already loaded for this path.

        Uses an asyncio lock so concurrent first-calls serialise rather
        than loading the model twice.
        """
        if self._loaded and self._model_path == model_path:
            return

        async with self._load_lock:
            # Double-check after acquiring the lock.
            if self._loaded and self._model_path == model_path:
                return

            _update_runner_status(RunnerStatus.WARMING)

            try:
                mlx_lm = self._import_mlx_lm()
                self._model, self._tokenizer = mlx_lm.load(model_path)
                self._model_path = model_path
                self._loaded = True
            except Exception:
                _update_runner_status(RunnerStatus.DEGRADED)
                raise

            _update_runner_status(RunnerStatus.READY)

    # ── Prompt formatting ───────────────────────────────────────────────

    def _format_chat_prompt(self, request: ChatCompletionRequest) -> str:
        """Convert OpenAI chat messages into a model-ready prompt string.

        Uses the tokenizer's chat template when available, falling back
        to a simple text transcript otherwise.
        """
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        if self._tokenizer is not None and hasattr(self._tokenizer, "apply_chat_template"):
            return self._tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=False,
            )

        # Fallback transcript for tokenizers without a chat template.
        parts: list[str] = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")
        parts.append("Assistant: ")
        return "\n".join(parts)

    # ── Chat completion (non-streaming) ─────────────────────────────────

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Run a non-streaming chat completion through mlx-lm."""

        if request.stream:
            raise StreamingNotSupportedError(
                "MLX streaming is not yet implemented.  See Phase 1F."
            )

        model_path = get_mlx_model_path()
        await self._ensure_loaded(model_path)

        prompt = self._format_chat_prompt(request)
        max_tokens = request.max_tokens or get_mlx_max_tokens_default()

        _update_runner_status(RunnerStatus.GENERATING)
        t0 = time.monotonic()

        try:
            mlx_lm = self._import_mlx_lm()
            gen_kwargs: dict = {
                "prompt": prompt,
                "max_tokens": max_tokens,
            }
            # mlx_lm.generate accepts ``temp`` (not ``temperature``).
            if request.temperature != 0.7:
                gen_kwargs["temp"] = request.temperature
            # top_p is not directly supported by mlx_lm.generate;
            # leave a TODO for later sampling refinement.

            raw = mlx_lm.generate(self._model, self._tokenizer, **gen_kwargs)
        finally:
            _update_runner_status(RunnerStatus.READY)

        elapsed_ms = (time.monotonic() - t0) * 1000.0

        request_id = f"chatcmpl-mlx-{uuid.uuid4().hex[:12]}"

        # Strip the prompt from the output if the generate call returns it.
        # mlx_lm.generate typically returns only the generated text, but
        # we are defensive.
        response_text = raw
        if isinstance(response_text, str) and response_text.startswith(prompt):
            response_text = response_text[len(prompt) :]

        return ChatCompletionResponse(
            id=request_id,
            object="chat.completion",
            created=int(time.time()),
            model=model_path,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=response_text),
                    finish_reason="stop",
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(prompt.split()) + len(response_text.split()),
            ),
        )

    # ── Codexify-style generate ─────────────────────────────────────────

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run a Codexify-style generation through mlx-lm."""

        model_path = get_mlx_model_path()
        await self._ensure_loaded(model_path)

        max_tokens = request.max_tokens
        t0 = time.monotonic()

        _update_runner_status(RunnerStatus.GENERATING)
        try:
            mlx_lm = self._import_mlx_lm()
            gen_kwargs: dict = {
                "prompt": request.prompt,
                "max_tokens": max_tokens,
            }
            if request.temperature != 0.7:
                gen_kwargs["temp"] = request.temperature

            raw = mlx_lm.generate(self._model, self._tokenizer, **gen_kwargs)
        finally:
            _update_runner_status(RunnerStatus.READY)

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        request_id = request.request_id or str(uuid.uuid4())

        response_text = raw
        if isinstance(response_text, str) and response_text.startswith(request.prompt):
            response_text = response_text[len(request.prompt) :]

        return GenerateResponse(
            ok=True,
            request_id=request_id,
            model_id=model_path,
            text=response_text,
            finish_reason="stop",
            usage=TokenUsage(
                prompt_tokens=len(request.prompt.split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(request.prompt.split()) + len(response_text.split()),
            ),
            runtime=ResponseRuntimeInfo(
                adapter=self.name,
                queued=False,
                elapsed_ms=round(elapsed_ms, 3),
            ),
        )


# ── Internal helpers ────────────────────────────────────────────────────────


def _update_runner_status(status: RunnerStatus) -> None:
    """Push a status change into the global RuntimeState, if available."""
    try:
        from whooshd.runtime import get_runtime

        rt = get_runtime()
        rt.status = status
    except Exception:
        pass  # best-effort; never let a status update crash inference

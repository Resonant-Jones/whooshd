"""MLX inference adapter.

Lazy-imports mlx-lm so the normal test suite never touches it.
Model loading is guarded by an asyncio lock so concurrent first requests
do not trigger duplicate loads.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncIterator, Optional

from whooshd.config import (
    get_mlx_max_tokens_default,
    get_mlx_model_path,
)
from whooshd.contracts import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    GenerateRequest,
    GenerateResponse,
    ModelLifecycleState,
    ResponseRuntimeInfo,
    RunnerStatus,
    TokenUsage,
)


class MLXInferenceAdapter:
    """Inference adapter backed by mlx-lm.

    Model and tokenizer are loaded lazily on the first request.  Subsequent
    requests reuse the cached objects.  Non-streaming and streaming chat
    completions are both supported.
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
        return True

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
            _update_model_lifecycle(ModelLifecycleState.WARMING)

            try:
                mlx_lm = self._import_mlx_lm()
                self._model, self._tokenizer = mlx_lm.load(model_path)
                self._model_path = model_path
                self._loaded = True
            except Exception:
                _update_runner_status(RunnerStatus.DEGRADED)
                _update_model_lifecycle(ModelLifecycleState.FAILED)
                raise

            _update_runner_status(RunnerStatus.READY)
            _update_model_lifecycle(ModelLifecycleState.READY)

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

    # ── Chat completion (streaming) ─────────────────────────────────────

    async def chat_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream chat completion chunks through mlx-lm.stream_generate."""

        model_path = get_mlx_model_path()
        await self._ensure_loaded(model_path)

        prompt = self._format_chat_prompt(request)
        max_tokens = request.max_tokens or get_mlx_max_tokens_default()

        request_id = f"chatcmpl-mlx-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # Chunk 1: assistant role marker, no content.
        await asyncio.sleep(0)  # yield control so the caller can observe state
        yield ChatCompletionChunk(
            id=request_id,
            created=created,
            model=model_path,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionDelta(role="assistant"),
                )
            ],
        )

        _update_runner_status(RunnerStatus.GENERATING)

        errored = False
        try:
            mlx_lm = self._import_mlx_lm()
            gen_kwargs: dict = {
                "prompt": prompt,
                "max_tokens": max_tokens,
            }
            if request.temperature != 0.7:
                gen_kwargs["temp"] = request.temperature

            for response in mlx_lm.stream_generate(
                self._model, self._tokenizer, **gen_kwargs
            ):
                text = response.text
                if text:
                    await asyncio.sleep(0)
                    yield ChatCompletionChunk(
                        id=request_id,
                        created=created,
                        model=model_path,
                        choices=[
                            ChatCompletionChunkChoice(
                                index=0,
                                delta=ChatCompletionDelta(content=text),
                            )
                        ],
                    )
        except Exception:
            _update_runner_status(RunnerStatus.DEGRADED)
            errored = True
            raise
        finally:
            if not errored:
                _update_runner_status(RunnerStatus.READY)

        # Final chunk: empty delta, finish_reason = stop.
        await asyncio.sleep(0)
        yield ChatCompletionChunk(
            id=request_id,
            created=created,
            model=model_path,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionDelta(),
                    finish_reason="stop",
                )
            ],
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

    # ── Lifecycle ───────────────────────────────────────────────────────

    def is_loaded(self) -> bool:
        return self._loaded

    def model_id(self) -> Optional[str]:
        return self._model_path

    async def warmup(self) -> None:
        """Load the model immediately (delegates to _ensure_loaded)."""
        model_path = get_mlx_model_path()
        await self._ensure_loaded(model_path)

    async def unload(self) -> None:
        """Release model and tokenizer references and hint the GC."""
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._model_path = None
        import gc
        gc.collect()


# ── Internal helpers ────────────────────────────────────────────────────────


def _update_runner_status(status: RunnerStatus) -> None:
    """Push a status change into the global RuntimeState, if available."""
    try:
        from whooshd.runtime import get_runtime

        rt = get_runtime()
        rt.status = status
    except Exception:
        pass  # best-effort; never let a status update crash inference


def _update_model_lifecycle(state: ModelLifecycleState) -> None:
    """Push a model lifecycle change into the global RuntimeState."""
    try:
        from whooshd.runtime import get_runtime

        rt = get_runtime()
        rt.model_lifecycle = state
    except Exception:
        pass

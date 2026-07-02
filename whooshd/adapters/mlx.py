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
from whooshd.adapters.mlx_prompt import extract_chat_messages, render_mlx_chat_prompt
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
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeKind,
    RuntimeModel,
    TokenUsage,
)


class MLXInferenceAdapter:
    """Inference adapter backed by mlx-lm.

    Model and tokenizer are loaded lazily on the first request.  Subsequent
    requests reuse the cached objects.  Non-streaming and streaming chat
    completions are both supported.
    """

    def __init__(self, tokenizer_registry: object = None, kv_backend_registry: object = None) -> None:
        self._model: object = None
        self._tokenizer: object = None
        self._model_path: Optional[str] = None
        self._load_lock = asyncio.Lock()
        self._loaded = False
        self._tokenizer_registry = tokenizer_registry
        self._kv_backend_registry = kv_backend_registry

    # ── Protocol properties ────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "mlx-lm"

    @property
    def kind(self) -> str:
        return RuntimeKind.MLX_LM.value

    @property
    def supports_streaming(self) -> bool:
        return True

    # ── Batch execution ────────────────────────────────────────────────

    def supports_chat_batching(self) -> str:
        """MLX reports experimental only when all gates pass."""
        from whooshd.config import (
            get_batch_execution_enabled,
            get_mlx_batch_execution_enabled,
        )
        if not get_batch_execution_enabled():
            return "unsupported"
        if not get_mlx_batch_execution_enabled():
            return "unsupported"
        try:
            from mlx_lm import batch_generate  # noqa: F401
            return "experimental"
        except ImportError:
            return "unsupported"

    async def chat_completion_batch(self, requests, contexts=None):
        """Execute a batch of chat completions through MLX batch_generate."""
        from mlx_lm import batch_generate
        from whooshd.adapters.mlx_prompt import render_mlx_chat_prompt, extract_chat_messages
        from whooshd.contracts import (
            ChatCompletionChoice, ChatCompletionResponse,
            ChatCompletionUsage, ChatMessage,
        )
        import uuid
        import time as _time

        if not requests:
            raise ValueError("Batch must contain at least one request")
        if not self._loaded or self._model is None or self._tokenizer is None:
            raise RuntimeError("MLX model not loaded")

        tokenized = []
        rendered = []
        for req in requests:
            messages = extract_chat_messages(req)
            prompt = render_mlx_chat_prompt(self._tokenizer, messages)
            rendered.append(prompt)
            ids = self._tokenizer.encode(prompt)
            if hasattr(ids, "ids"):
                tokenized.append(list(ids.ids))
            else:
                tokenized.append(list(ids))

        max_tokens = max((r.max_tokens or 256) for r in requests)
        result = batch_generate(self._model, self._tokenizer, prompts=tokenized, max_tokens=max_tokens)
        texts = result.texts if hasattr(result, "texts") else getattr(result, "outputs", [])
        if not isinstance(texts, (list, tuple)):
            texts = [texts]
        if len(texts) != len(requests):
            raise RuntimeError(f"MLX returned {len(texts)} outputs for {len(requests)} requests")

        responses = []
        for i, (req, text) in enumerate(zip(requests, texts)):
            responses.append(ChatCompletionResponse(
                id=f"chatcmpl-mlx-batch-{uuid.uuid4().hex[:8]}",
                object="chat.completion", created=int(_time.time()), model=req.model,
                choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content=text), finish_reason="stop")],
                usage=ChatCompletionUsage(prompt_tokens=len(rendered[i].split()) if i < len(rendered) else 0, completion_tokens=len(text.split()), total_tokens=(len(rendered[i].split()) if i < len(rendered) else 0) + len(text.split())),
            ))
        return responses

    @property
    def tokenizer(self) -> object | None:
        """Return the loaded tokenizer, or None."""
        return self._tokenizer

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

                # Register MLX tokenizer adapter for ThreadWake if enabled
                if self._tokenizer_registry is not None and self._tokenizer is not None:
                    self._register_tokenizer_adapter()

                # Register MLX KV backend skeleton for ThreadWake boundary.
                if self._kv_backend_registry is not None:
                    self._register_kv_adapter()
            except Exception:
                _update_runner_status(RunnerStatus.DEGRADED)
                _update_model_lifecycle(ModelLifecycleState.FAILED)
                raise

            _update_runner_status(RunnerStatus.READY)
            _update_model_lifecycle(ModelLifecycleState.READY)

    # ── Prompt formatting ───────────────────────────────────────────────

    def _format_chat_prompt(self, request: ChatCompletionRequest) -> str:
        """Convert OpenAI chat messages into a model-ready prompt string.

        Uses the shared MLX prompt renderer so that inference and
        ThreadWake tokenization use identical rendering.
        """
        messages = extract_chat_messages(request)
        return render_mlx_chat_prompt(self._tokenizer, messages)

    # ── Chat completion (non-streaming) ─────────────────────────────────

    async def chat_completion(self, request: ChatCompletionRequest, context=None) -> ChatCompletionResponse:
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
        self, request: ChatCompletionRequest, context=None
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream chat completion chunks through mlx-lm.stream_generate.

        Checks the cancellation token in *context* between yielded
        responses and stops cleanly when cancellation is requested.
        Attempts to close the stream generator on early termination.
        """

        model_path = get_mlx_model_path()
        await self._ensure_loaded(model_path)

        prompt = self._format_chat_prompt(request)
        max_tokens = request.max_tokens or get_mlx_max_tokens_default()

        request_id = f"chatcmpl-mlx-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # Check cancellation before starting.
        if context and context.cancellation_token.is_cancelled():
            return

        # Chunk 1: assistant role marker, no content.
        await asyncio.sleep(0)
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

        stream = None
        errored = False
        try:
            mlx_lm = self._import_mlx_lm()
            gen_kwargs: dict = {
                "prompt": prompt,
                "max_tokens": max_tokens,
            }
            if request.temperature != 0.7:
                gen_kwargs["temp"] = request.temperature

            stream = mlx_lm.stream_generate(
                self._model, self._tokenizer, **gen_kwargs
            )
            for response in stream:
                if context and context.cancellation_token.is_cancelled():
                    break
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
            if stream is not None:
                close_fn = getattr(stream, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception:
                        pass
            if not errored:
                _update_runner_status(RunnerStatus.READY)

        # Cancelled or completed — no final chunk if cancelled.
        if context and context.cancellation_token.is_cancelled():
            return

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
        # Clear tokenizer adapter registration
        if self._tokenizer_registry is not None:
            try:
                self._tokenizer_registry.unregister("mlx")
            except Exception:
                pass
        # Clear KV backend registration
        if self._kv_backend_registry is not None:
            try:
                self._kv_backend_registry.unregister("mlx")
            except Exception:
                pass
        import gc
        gc.collect()

    def _register_tokenizer_adapter(self) -> None:
        """Register the MLX tokenizer adapter for ThreadWake.

        Called after model/tokenizer load.  Best-effort — errors
        are logged but never crash inference.
        """
        try:
            from whooshd.config import get_threadwake_mlx_tokenizer_enabled
            if not get_threadwake_mlx_tokenizer_enabled():
                return
            from whooshd.runtime.threadwake.mlx_tokenizer import MLXInProcessTokenizerAdapter
            adapter = MLXInProcessTokenizerAdapter(tokenizer=self._tokenizer)
            self._tokenizer_registry.register("mlx", adapter)
        except Exception:
            pass  # Best-effort; never crash inference for observability wiring

    def _register_kv_adapter(self) -> None:
        """Register the MLX KV backend skeleton for ThreadWake.

        The skeleton reports unsupported — this establishes the adapter
        boundary without enabling real KV reuse yet.
        """
        try:
            from whooshd.runtime.threadwake.mlx_kv import MLXKVBackendAdapter
            adapter = MLXKVBackendAdapter(
                model=self._model,
                tokenizer=self._tokenizer,
            )
            self._kv_backend_registry.register("mlx", adapter)
        except Exception:
            pass  # Best-effort; never crash inference for observability wiring

    # ── Multi-runtime introspection ──────────────────────────────────

    async def health(self) -> RuntimeHealth:
        """Return the current health state of this MLX runtime."""
        if not self._loaded:
            return RuntimeHealth(
                kind=self.kind,
                enabled=True,
                state=RuntimeHealthState.OFFLINE,
                active_model=None,
                detail="Model not loaded.",
            )
        return RuntimeHealth(
            kind=self.kind,
            enabled=True,
            state=RuntimeHealthState.READY,
            active_model=self._model_path,
            detail="Model loaded and ready.",
        )

    async def list_models(self) -> list[RuntimeModel]:
        """Return models managed by this MLX runtime."""
        model_path = self._model_path or get_mlx_model_path()
        runtime = self.kind
        loaded = self._loaded
        state = RuntimeHealthState.READY.value if loaded else RuntimeHealthState.OFFLINE.value

        return [
            RuntimeModel(
                id=model_path,
                display_name=model_path.rsplit("/", 1)[-1] if "/" in model_path else model_path,
                runtime=runtime,
                format="mlx",
                path=model_path,
                context_window=None,
                supports_tools=False,
                supports_vision=False,
                supports_reasoning=False,
                loaded=loaded,
                state=state,
            )
        ]


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

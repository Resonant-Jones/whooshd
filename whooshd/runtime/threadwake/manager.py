"""ThreadWake observe-mode manager.

The manager compiles prompt graphs, evaluates policy, reports
backend KV capability, maintains an in-memory metadata index,
and (Phase D) executes ephemeral KV reuse for identical prefixes.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from whooshd.config import (
    get_threadwake_allow_global,
    get_threadwake_bytes_per_token,
    get_threadwake_default_scope,
    get_threadwake_enabled,
    get_threadwake_max_entries,
    get_threadwake_max_memory_mb,
    get_threadwake_min_prefix_tokens,
    get_threadwake_mode,
)

from .backend import BackendKVAdapterRegistry, KVCapableBackend, NoOpKVBackendAdapter
from .compiler import compile_prompt_graph, canonicalize_content
from .handles import KVCapability, KVHandle
from .index import EntryStatus, ScopeContext, ThreadWakeIndex
from .keys import build_threadwake_cache_key
from .metrics import ThreadWakeMetrics, get_threadwake_metrics
from .policy import evaluate_threadwake_policy
from .kv_lifecycle import KVEvent, KVLifecycleObserver
from .replay_analysis import CandidateReplayAnalyzer
from .tokenization import (
    BackendTokenizerAdapterRegistry,
    NoOpTokenizerAdapter,
    TokenizedPrompt,
)
from .types import (
    EphemeralResult,
    PromptGraph,
    ThreadWakeMetadata,
    ThreadWakeMode,
    ThreadWakeObservation,
    ThreadWakeRequestConfig,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

# Legacy synthetic token helpers — retained only for FakeKVBackend test
# compatibility.  Production code MUST use TokenizedPrompt.stable_prefix_token_ids
# and TokenizedPrompt.dynamic_tail_token_ids from a real tokenizer adapter.


def _extract_stable_prefix_tokens(graph: PromptGraph) -> list[str]:
    """LEGACY: Synthetic token placeholder extraction.  Tests only."""
    tokens: list[str] = []
    for segment in graph.segments:
        if segment.in_stable_prefix:
            for i in range(segment.token_count):
                tokens.append(f"{segment.name}:{i}")
    return tokens


def _extract_dynamic_tail_tokens(graph: PromptGraph) -> list[str]:
    """LEGACY: Synthetic token placeholder extraction.  Tests only."""
    tokens: list[str] = []
    for segment in graph.segments:
        if not segment.in_stable_prefix:
            for i in range(segment.token_count):
                tokens.append(f"{segment.name}:{i}")
    return tokens


def _extract_all_tokens(graph: PromptGraph) -> list[str]:
    """LEGACY: Synthetic token placeholder extraction.  Tests only."""
    tokens: list[str] = []
    for segment in graph.segments:
        for i in range(segment.token_count):
            tokens.append(f"{segment.name}:{i}")
    return tokens


def _extract_dynamic_tail_tokens_from_segments(segments) -> list[str]:
    """LEGACY: Synthetic token placeholder extraction.  Tests only."""
    tokens: list[str] = []
    for segment in segments:
        for i in range(segment.token_count):
            tokens.append(f"{segment.name}:{i}")
    return tokens


class ThreadWakeManager:
    """ThreadWake facade for Phase D.

    Phase D adds ephemeral KV reuse execution for identical prefixes
    on backends that support resumable KV.  Unsupported backends
    degrade safely to the full prefill path.
    """

    def __init__(
        self,
        metrics: ThreadWakeMetrics | None = None,
        backend_registry: BackendKVAdapterRegistry | None = None,
        tokenizer_registry: BackendTokenizerAdapterRegistry | None = None,
        kv_observer: KVLifecycleObserver | None = None,
        index: ThreadWakeIndex | None = None,
    ) -> None:
        self.metrics = metrics or get_threadwake_metrics()
        self._backend_registry = backend_registry or BackendKVAdapterRegistry()
        self._tokenizer_registry = tokenizer_registry or BackendTokenizerAdapterRegistry()
        self._kv_observer = kv_observer or KVLifecycleObserver(enabled=False)
        self._index = index or self._build_default_index()

    @staticmethod
    def _build_default_index() -> ThreadWakeIndex:
        return ThreadWakeIndex(
            max_entries=get_threadwake_max_entries(),
            max_memory_bytes=get_threadwake_max_memory_mb() * 1024 * 1024,
            bytes_per_token=get_threadwake_bytes_per_token(),
            allow_global=get_threadwake_allow_global(),
        )

    def observe_request(
        self,
        request: Any,
        *,
        backend: str | None,
        model_revision: str | None = None,
        quantization: str | None = None,
        tokenizer_hash: str | None = None,
        chat_template_hash: str | None = None,
    ) -> ThreadWakeObservation:
        """Return a safe observation for a chat completion request.

        ``model_revision`` and ``quantization`` are accepted for future key
        material, but Phase A intentionally performs no KV lookup.
        """

        config = self._resolve_config(getattr(request, "threadwake", None))
        if not config.enabled or config.mode == ThreadWakeMode.OFF:
            observation = evaluate_threadwake_policy(None, config)
            observation = self._attach_backend_kv_status(observation, backend)
            self.metrics.record(observation)
            return observation

        graph = self.compile_prompt_graph(
            request,
            backend=backend,
            tokenizer_hash=tokenizer_hash,
            chat_template_hash=chat_template_hash,
            scope=config.scope or "thread",
        )
        observation = self.evaluate_policy(graph, config)
        observation = self._attach_backend_kv_status(observation, backend)
        self._record_index_observation(
            graph=graph,
            observation=observation,
            backend=backend,
            model_revision=model_revision,
            quantization=quantization,
            scope_context=self._extract_scope_context(request),
        )
        self.metrics.record(observation)
        return observation

    def compile_prompt_graph(
        self,
        request: Any,
        *,
        backend: str | None,
        tokenizer_hash: str | None = None,
        chat_template_hash: str | None = None,
        scope: str = "thread",
    ) -> PromptGraph:
        codexify_segments = getattr(request, "threadwake_segments", None)
        return compile_prompt_graph(
            messages=list(getattr(request, "messages", [])),
            model_id=getattr(request, "model", None),
            backend=backend,
            tools=getattr(request, "tools", None),
            tool_choice=getattr(request, "tool_choice", None),
            tokenizer_hash=tokenizer_hash,
            chat_template_hash=chat_template_hash,
            scope=scope,  # type: ignore[arg-type]
            codexify_segments=codexify_segments,
            allow_global=self._index.allow_global if hasattr(self, '_index') else False,
        )

    def evaluate_policy(
        self,
        graph: PromptGraph,
        config: ThreadWakeRequestConfig,
    ) -> ThreadWakeObservation:
        return evaluate_threadwake_policy(graph, config)

    def _attach_backend_kv_status(
        self,
        observation: ThreadWakeObservation,
        backend: str | None,
        tokenized: TokenizedPrompt | None = None,
    ) -> ThreadWakeObservation:
        """Annotate observation with backend KV capability + tokenizer info."""
        if backend is None:
            observation.backend_kv_capability = None
            observation.can_reuse_kv = False
            observation.kv_reuse_reason = "backend_unknown"
            self._attach_tokenizer_status(observation, backend)
            return observation

        capability = self._backend_registry.capability(backend)
        observation.backend_kv_capability = capability.value
        self._kv_observer.record_capability(backend=backend, capability=capability.value)

        if capability.value == "unsupported":
            observation.can_reuse_kv = False
            observation.kv_reuse_reason = "backend_unsupported"
        elif not observation.eligible:
            observation.can_reuse_kv = False
            observation.kv_reuse_reason = (
                f"backend_capable_but_ineligible: {observation.reason}"
            )
        elif observation.mode in (ThreadWakeMode.EPHEMERAL, ThreadWakeMode.SESSION):
            # Ephemeral/session mode: KV reuse only if real tokenization available
            if tokenized and tokenized.real_tokenization:
                observation.can_reuse_kv = True
                observation.kv_reuse_reason = None
                observation.stable_prefix_token_count_real = tokenized.stable_prefix_token_count
                observation.dynamic_tail_token_count_real = tokenized.dynamic_tail_token_count
            else:
                observation.can_reuse_kv = False
                observation.kv_reuse_reason = "real_tokenization_unavailable"
        else:
            observation.can_reuse_kv = False
            observation.kv_reuse_reason = "observe_mode_not_reusing"

        self._attach_tokenizer_status(observation, backend, tokenized)
        return observation

    def _attach_tokenizer_status(
        self,
        observation: ThreadWakeObservation,
        backend: str | None,
        tokenized: TokenizedPrompt | None = None,
    ) -> None:
        """Annotate observation with tokenizer capability info."""
        if backend is None:
            observation.tokenizer_capability = None
            observation.real_tokenization_available = False
            observation.tokenization_reason = "backend_unknown"
            return

        cap = self._tokenizer_registry.capability(backend)
        observation.tokenizer_capability = cap.value

        if cap.value == "unsupported":
            observation.real_tokenization_available = False
            observation.tokenization_reason = "tokenizer_unsupported"
        elif cap.value == "estimates_only":
            observation.real_tokenization_available = False
            observation.tokenization_reason = "tokenizer_estimates_only"
        elif tokenized and tokenized.real_tokenization:
            observation.real_tokenization_available = True
            observation.tokenization_reason = None
            if tokenized.stable_prefix_token_count:
                observation.stable_prefix_token_count_real = tokenized.stable_prefix_token_count
            if tokenized.dynamic_tail_token_count:
                observation.dynamic_tail_token_count_real = tokenized.dynamic_tail_token_count
        else:
            # Adapter is registered with token_ids capability but not yet called
            observation.real_tokenization_available = True
            observation.tokenization_reason = None

    def _record_index_observation(
        self,
        *,
        graph: PromptGraph,
        observation: ThreadWakeObservation,
        backend: str | None,
        model_revision: str | None,
        quantization: str | None,
        scope_context: ScopeContext,
    ) -> None:
        """Record an eligible observation in the metadata index."""
        if not observation.eligible:
            return
        if backend is None:
            return

        scope = observation.cache_scope
        cache_key = build_threadwake_cache_key(
            graph,
            scope=scope,
            model_revision=model_revision,
            quantization=quantization,
        )
        try:
            self._index.put_observation(
                cache_key=cache_key,
                model_id=graph.model_id or "",
                backend=backend,
                prompt_prefix_hash=graph.stable_prefix_hash,
                token_count=graph.stable_prefix_tokens,
                scope=scope,
                scope_context=scope_context,
            )
        except ValueError:
            # Global scope disallowed — skip index insertion
            pass

    @staticmethod
    def _extract_scope_context(request: Any) -> ScopeContext:
        """Extract scope identifiers from a request object.

        Looks for common metadata patterns in the request.
        """
        return ScopeContext(
            thread_id=getattr(request, "thread_id", None),
            user_id=getattr(request, "user_id", None),
            project_id=getattr(request, "project_id", None),
        )

    # ── Ephemeral KV reuse ──────────────────────────────────────────────

    def execute_ephemeral(
        self,
        request: Any,
        *,
        backend: str,
        model_revision: str | None = None,
        quantization: str | None = None,
        tokenizer_hash: str | None = None,
        chat_template_hash: str | None = None,
        generate_fn: Callable[[Any, dict[str, Any]], list[str]],
        generation_params: dict[str, Any] | None = None,
    ) -> EphemeralResult:
        """Execute ephemeral KV reuse for a chat completion request."""
        gen_params = generation_params or {}
        scope_context = self._extract_scope_context(request)

        config = self._resolve_config(getattr(request, "threadwake", None))
        graph = self.compile_prompt_graph(
            request, backend=backend,
            tokenizer_hash=tokenizer_hash,
            chat_template_hash=chat_template_hash,
            scope=config.scope or "thread",
        )
        observation = self.evaluate_policy(graph, config)

        # Tokenize the prompt (may produce real or estimate-only spans)
        tokenizer = self._tokenizer_registry.get(backend)
        tokenized = tokenizer.tokenize_prompt(graph, request, model_id=graph.model_id or "")
        if tokenized is None:
            tokenized = TokenizedPrompt(real_tokenization=False, unavailable_reason="tokenizer_returned_none")

        observation = self._attach_backend_kv_status(observation, backend, tokenized)

        if config.mode not in (ThreadWakeMode.EPHEMERAL, ThreadWakeMode.SESSION) or not observation.eligible:
            self.metrics.record(observation)
            return self._full_generation_result(generate_fn, request, gen_params, observation)

        scope = observation.cache_scope
        cache_key = build_threadwake_cache_key(
            graph, scope=scope, model_revision=model_revision, quantization=quantization,
        )

        kv_adapter = self._backend_registry.get(backend)
        can_reuse = kv_adapter.supports_kv_cache() in (
            KVCapability.RESUMABLE, KVCapability.CLONEABLE, KVCapability.SERIALIZABLE,
        )
        if not can_reuse or not tokenized.real_tokenization:
            if not tokenized.real_tokenization:
                observation.can_reuse_kv = False
                observation.kv_reuse_reason = tokenized.unavailable_reason or "real_tokenization_unavailable"
            self.metrics.record(observation)
            return self._full_generation_result(generate_fn, request, gen_params, observation)

        # ── Session continuation: try thread-tip monotonic append ───────
        thread_id = scope_context.thread_id
        if thread_id and graph.continuation_candidate:
            session_result = self._try_session_continuation(
                graph=graph, thread_id=thread_id, scope_context=scope_context,
                kv_adapter=kv_adapter, backend=backend,
                observation=observation, generate_fn=generate_fn,
                request=request, gen_params=gen_params, tokenized=tokenized,
            )
            if session_result is not None:
                return session_result

        # ── Ephemeral exact-prefix hit ──────────────────────────────────
        index_entry = self._index.get(cache_key, scope_context)
        if index_entry is not None and index_entry.status == EntryStatus.READY:
            return self._ephemeral_hit(
                graph=graph, cache_key=cache_key, index_entry=index_entry,
                kv_adapter=kv_adapter, observation=observation,
                generate_fn=generate_fn, request=request, gen_params=gen_params,
                tokenized=tokenized,
            )

        return self._ephemeral_miss(
            graph=graph, cache_key=cache_key, scope=scope, scope_context=scope_context,
            kv_adapter=kv_adapter, observation=observation,
            generate_fn=generate_fn, request=request, gen_params=gen_params, backend=backend,
            tokenized=tokenized,
        )

    def _ephemeral_hit(self, *, graph, cache_key, index_entry, kv_adapter, observation, generate_fn, request, gen_params, tokenized=None) -> EphemeralResult:
        logger.debug("ThreadWake ephemeral HIT cache_key=%s", cache_key)
        if tokenized is None or not tokenized.real_tokenization:
            raise RuntimeError("BUG: _ephemeral_hit called without real tokenization — gate in execute_ephemeral should have prevented this")
        try:
            opaque = {"index_key": cache_key, "handle_id": index_entry.kv_handle_id}
            kv_handle = KVHandle(
                backend=index_entry.backend, model_id=index_entry.model_id,
                token_count=index_entry.token_count, opaque_ref=opaque,
            )
            cloned = kv_adapter.clone_kv(kv_handle)
            dynamic_tokens = [str(t) for t in tokenized.dynamic_tail_token_ids]
            output = list(kv_adapter.generate_from_kv(cloned, dynamic_tokens, gen_params))
            self._kv_observer.record_cloned(
                backend=index_entry.backend, model_id=index_entry.model_id,
                kv_handle_id=index_entry.kv_handle_id,
            )
            self._kv_observer.record_reused(
                backend=index_entry.backend, model_id=index_entry.model_id,
                token_count=len(dynamic_tokens),
            )
            observation.cache_hit = True
            observation.estimated_prefill_reuse_tokens = graph.stable_prefix_tokens
            self.metrics.record(observation)
            return EphemeralResult(
                output_tokens=output, cache_hit=True, matched_tokens=graph.stable_prefix_tokens,
                observation=observation,
                metadata=ThreadWakeMetadata(
                    cache_hit=True, matched_tokens=graph.stable_prefix_tokens,
                    mode="ephemeral", scope=observation.cache_scope,
                    backend_kv_capability=observation.backend_kv_capability,
                ),
            )
        except Exception as exc:
            logger.warning("ThreadWake ephemeral HIT failed: %s — falling back", exc)
            self._index.mark_stale(cache_key)
            return self._full_generation_result(generate_fn, request, gen_params, observation)

    def _ephemeral_miss(self, *, graph, cache_key, scope, scope_context, kv_adapter, observation, generate_fn, request, gen_params, backend, tokenized=None) -> EphemeralResult:
        logger.debug("ThreadWake ephemeral MISS cache_key=%s", cache_key)
        result = self._full_generation_result(generate_fn, request, gen_params, observation)
        if tokenized is None or not tokenized.real_tokenization:
            return result  # No real tokens → skip prefill storage, return full generation result
        try:
            stable_tokens = [str(t) for t in tokenized.stable_prefix_token_ids]
            if stable_tokens:
                kv_handle = kv_adapter.prefill_to_kv(stable_tokens, model_id=graph.model_id or "")
                self._index.put_observation(
                    cache_key=cache_key, model_id=graph.model_id or "", backend=backend,
                    prompt_prefix_hash=graph.stable_prefix_hash,
                    token_count=graph.stable_prefix_tokens, scope=scope,
                    scope_context=scope_context, kv_handle_id=kv_handle.id,
                )
                self._index.mark_ready(cache_key)
                self._kv_observer.record_created(
                    backend=backend, model_id=graph.model_id or "",
                    token_count=len(stable_tokens), kv_handle_id=kv_handle.id,
                    cache_key=cache_key,
                )

                # ── Store thread tip for session continuation ──────────
                thread_id = scope_context.thread_id
                if thread_id and graph.continuation_candidate:
                    self._index.store_thread_tip(
                        thread_id=thread_id,
                        model_id=graph.model_id or "",
                        backend=backend,
                        chain_hash=graph.full_prefix_chain_hash,
                        ordered_segment_hashes=graph.ordered_segment_hashes,
                        kv_handle_id=kv_handle.id,
                    )
        except Exception as exc:
            logger.warning("ThreadWake ephemeral MISS store failed: %s", exc)
        return result

    def _try_session_continuation(
        self, *, graph, thread_id, scope_context, kv_adapter, backend,
        observation, generate_fn, request, gen_params, tokenized=None,
    ) -> EphemeralResult | None:
        """Attempt session continuation via thread-tip monotonic append.

        Returns an EphemeralResult on success, or None if continuation
        is not possible (caller should fall through to ephemeral hit/miss).

        Requires real_tokenization=True — the gate in execute_ephemeral
        prevents calling this method without a real TokenizedPrompt.
        """
        if tokenized is None or not tokenized.real_tokenization:
            return None  # Cannot continue without real token spans
        model_id = graph.model_id or ""
        tip = self._index.get_latest_for_thread(thread_id, model_id, backend)
        if tip is None:
            # No previous tip — store one after generation
            return None

        if not self._index.validate_monotonic_append(
            tip.ordered_segment_hashes, graph.ordered_segment_hashes,
        ):
            # Non-monotonic: history was edited or truncated
            observation.kv_reuse_reason = "non_monotonic_or_changed_prefix"
            # Clear stale tip so next request can start fresh
            self._index.clear_thread_tip(thread_id, model_id, backend)
            return None

        # Valid continuation: compute appended segments only
        prev_count = tip.segment_count
        appended_segments = graph.segments[prev_count:]
        appended_tokens = sum(seg.token_count for seg in appended_segments)

        logger.debug(
            "ThreadWake session CONTINUATION thread=%s prev_segments=%d new_segments=%d appended_tokens=%d",
            thread_id[:8] if len(thread_id) > 8 else thread_id,
            prev_count, len(appended_segments), appended_tokens,
        )

        try:
            # Build a KV handle from the previous tip
            opaque = {"tip_key": tip.chain_hash, "handle_id": tip.kv_handle_id}
            kv_handle = KVHandle(
                backend=backend, model_id=model_id,
                token_count=tip.segment_count,
                opaque_ref=opaque,
            )
            cloned = kv_adapter.clone_kv(kv_handle)
            # Generate from appended dynamic tokens only (real token IDs required)
            append_token_list = [str(t) for t in tokenized.dynamic_tail_token_ids]
            output = list(kv_adapter.generate_from_kv(cloned, append_token_list, gen_params))

            observation.cache_hit = True
            observation.estimated_prefill_reuse_tokens = sum(
                seg.token_count for seg in graph.segments[:prev_count]
            )
            self.metrics.record(observation)

            # Update the thread tip with new chain hash
            self._index.store_thread_tip(
                thread_id=thread_id, model_id=model_id, backend=backend,
                chain_hash=graph.full_prefix_chain_hash,
                ordered_segment_hashes=graph.ordered_segment_hashes,
                kv_handle_id=tip.kv_handle_id,
            )

            return EphemeralResult(
                output_tokens=output, cache_hit=True,
                matched_tokens=observation.estimated_prefill_reuse_tokens,
                observation=observation,
                metadata=ThreadWakeMetadata(
                    cache_hit=True,
                    matched_tokens=observation.estimated_prefill_reuse_tokens,
                    mode="session", scope=observation.cache_scope,
                    backend_kv_capability=observation.backend_kv_capability,
                ),
            )
        except Exception as exc:
            logger.warning("ThreadWake session continuation failed: %s — falling back", exc)
            self._index.clear_thread_tip(thread_id, model_id, backend)
            return None  # Fall through to ephemeral hit/miss

    def _full_generation_result(self, generate_fn, request, gen_params, observation) -> EphemeralResult:
        output = generate_fn(request, gen_params)
        self.metrics.record(observation)
        return EphemeralResult(
            output_tokens=output, cache_hit=False, matched_tokens=0,
            observation=observation,
            metadata=ThreadWakeMetadata(
                cache_hit=False, matched_tokens=0,
                mode=observation.mode.value, scope=observation.cache_scope,
                backend_kv_capability=observation.backend_kv_capability,
            ),
        )

    # ── Async route-level bridge ─────────────────────────────────────

    async def execute_ephemeral_chat_completion(
        self,
        request: Any,
        *,
        backend: str,
        full_generation_fn: Callable[[], Awaitable],
        model_revision: str | None = None,
        quantization: str | None = None,
        tokenizer_hash: str | None = None,
        chat_template_hash: str | None = None,
        generation_params: dict[str, Any] | None = None,
    ) -> Any | None:
        """Async bridge: attempt ephemeral KV reuse, fall back to full generation.

        Returns a ChatCompletionResponse on cache hit, or None if the caller
        should use the normal adapter execution path (miss, unsupported backend,
        or mode not ephemeral/session).

        This method is designed for the FastAPI route layer.  It wraps
        ``execute_ephemeral`` with async-safe fallback behavior.
        """
        from whooshd.contracts import (
            ChatCompletionChoice,
            ChatCompletionResponse,
            ChatCompletionUsage,
            ChatMessage,
        )
        from whooshd.runtime.threadwake.types import ThreadWakeMode
        from whooshd.runtime.threadwake.handles import KVCapability
        import time as _time
        import uuid as _uuid

        config = self._resolve_config(getattr(request, "threadwake", None))

        # Only attempt ephemeral in ephemeral/session modes.
        if config.mode not in (ThreadWakeMode.EPHEMERAL, ThreadWakeMode.SESSION):
            return None

        # Check backend KV capability.
        kv_cap = self._backend_registry.capability(backend)
        if kv_cap.value == "unsupported":
            return None

        # Check backend tokenizer capability.
        tok_cap = self._tokenizer_registry.capability(backend)
        if tok_cap.value in ("unsupported", "estimates_only"):
            return None

        gen_params = generation_params or {}

        # Build a sync generate_fn that returns the fallback tokens.
        # This is only called on miss / full generation path in execute_ephemeral.
        def _sync_gen(_req, _params):
            return ["__tw_fallback__"]

        try:
            result = self.execute_ephemeral(
                request,
                backend=backend,
                model_revision=model_revision,
                quantization=quantization,
                tokenizer_hash=tokenizer_hash,
                chat_template_hash=chat_template_hash,
                generate_fn=_sync_gen,
                generation_params=gen_params,
            )
        except Exception:
            logger.warning("ThreadWake execute_ephemeral raised, falling back")
            return None

        if not result.cache_hit:
            # Miss — caller should use normal adapter execution.
            # The ephemeral miss path already stored the KV entry if possible.
            return None

        # ── Cache hit — convert output tokens to ChatCompletionResponse ──
        try:
            text = "".join(result.output_tokens) if result.output_tokens else ""
            if not text:
                text = "[ThreadWake cached response]"

            prompt_text = " ".join(
                m.content if hasattr(m, "content") else ""
                for m in getattr(request, "messages", [])
            )
            prompt_tokens = max(1, len(prompt_text.split()))
            completion_tokens = len(text.split()) if text else 1

            return ChatCompletionResponse(
                id=f"chatcmpl-tw-{_uuid.uuid4().hex[:12]}",
                object="chat.completion",
                created=int(_time.time()),
                model=getattr(request, "model", "unknown"),
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatMessage(role="assistant", content=text),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
            )
        except Exception:
            logger.warning("ThreadWake cache hit response build failed, falling back")
            return None

    # ── Health / admin ─────────────────────────────────────────────────

    def get_health(self) -> dict[str, Any]:
        """Return ThreadWake health and index stats.

        Safe for external consumption — no raw prompt content or
        opaque KV refs.  Includes status, ready/stale breakdown,
        and backend capability summary.
        """
        stats = self._index.stats()
        mode = get_threadwake_mode()
        status = self._compute_status(mode, stats)

        ready_entries = stats.entries_by_status.get("ready", 0)
        stale_entries = stats.entries_by_status.get("stale", 0)
        total_hits = stats.hit_count
        total_misses = stats.miss_count

        kv_stats = self._kv_observer.stats()
        return {
            "enabled": get_threadwake_enabled(),
            "mode": mode,
            "status": status,
            "entry_count": stats.entry_count,
            "ready_entries": ready_entries,
            "stale_entries": stale_entries,
            "max_entries": stats.max_entries,
            "estimated_memory_bytes": stats.estimated_memory_bytes,
            "max_memory_bytes": stats.max_memory_bytes,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "total_evictions": stats.evictions,
            "global_allowed": stats.global_allowed,
            "backend_capabilities": self._backend_capability_summary(),
            "kv_observability": {
                "enabled": self._kv_observer.enabled,
                "events_total": kv_stats.events_total,
                "events_by_type": kv_stats.events_by_type,
                "active_handles_estimate": kv_stats.active_handles_estimate,
                "created_total": kv_stats.created_total,
                "cloned_total": kv_stats.cloned_total,
                "reused_total": kv_stats.reused_total,
                "released_total": kv_stats.released_total,
                "errors_total": kv_stats.errors_total,
            },
            "entries_by_status": stats.entries_by_status,
            "entries_by_scope": stats.entries_by_scope,
            "candidate_registry": self._index.candidate_stats(),
            "candidate_replay": self._build_replay_summary(),
        }

    def _build_replay_summary(self) -> dict[str, Any]:
        try:
            analyzer = CandidateReplayAnalyzer()
            summary = analyzer.analyze_index(self._index, limit=20)
            return summary.safe_dict()
        except Exception:
            return {"total_candidates": 0}

    @staticmethod
    def _compute_status(mode: str, stats: Any) -> str:
        """Derive a status from actual index state.

        Mode is reported separately; status reflects cache health:
        - ``off``: no entries and mode is off or disabled
        - ``observing``: entries present but none marked ready
        - ``ready``: at least one ready entry exists
        - ``degraded``: more stale entries than ready entries
        """
        if stats.entry_count == 0:
            if mode == "off":
                return "off"
            return "observing"
        ready = stats.entries_by_status.get("ready", 0)
        stale = stats.entries_by_status.get("stale", 0)
        if stale > ready and stale > 0:
            return "degraded"
        if ready > 0:
            return "ready"
        return "observing"

    def _backend_capability_summary(self) -> dict[str, str]:
        """Return a map of backend name → capability string."""
        return {
            backend: self._backend_registry.capability(backend).value
            for backend in self._backend_registry.registered_backends()
        }

    def flush_cache(
        self,
        scope: str | None = None,
        *,
        model_id: str | None = None,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Flush the metadata index with optional filters.

        Returns ``{"flushed": N, "remaining": M}``.
        """
        scope_id_hashed: str | None = None
        if scope_id is not None:
            from .keys import sha256_hex
            scope_id_hashed = sha256_hex(scope_id)

        before = self._index.stats().entry_count
        removed = self._index.flush(
            scope=scope,
            model_id=model_id,
            scope_id_hashed=scope_id_hashed,
        )
        after = self._index.stats().entry_count
        return {"flushed": removed, "remaining": after}

    def _resolve_config(self, raw: Any) -> ThreadWakeRequestConfig:
        env_enabled = get_threadwake_enabled()
        env_mode = _coerce_mode(get_threadwake_mode())
        env_scope = get_threadwake_default_scope()
        env_min_tokens = get_threadwake_min_prefix_tokens()

        if raw is None:
            return ThreadWakeRequestConfig(
                enabled=env_enabled,
                mode=env_mode,
                scope=env_scope,  # type: ignore[arg-type]
                min_stable_prefix_tokens=env_min_tokens,
            )

        if isinstance(raw, ThreadWakeRequestConfig):
            provided = raw
        else:
            provided = ThreadWakeRequestConfig.model_validate(raw)

        requested_mode = provided.mode
        if requested_mode is None:
            requested_mode = ThreadWakeMode.OBSERVE if provided.enabled else env_mode

        if provided.enabled is None:
            enabled = bool(env_enabled)
            if provided.mode is not None:
                enabled = provided.mode != ThreadWakeMode.OFF
        else:
            enabled = provided.enabled

        return ThreadWakeRequestConfig(
            enabled=enabled,
            mode=requested_mode,
            scope=provided.scope or env_scope,  # type: ignore[arg-type]
            metadata=dict(provided.metadata),
            min_stable_prefix_tokens=(
                provided.min_stable_prefix_tokens
                if provided.min_stable_prefix_tokens is not None
                else env_min_tokens
            ),
        )


def _coerce_mode(value: str | ThreadWakeMode | None) -> ThreadWakeMode:
    if isinstance(value, ThreadWakeMode):
        return value
    try:
        return ThreadWakeMode(value or ThreadWakeMode.OFF.value)
    except ValueError:
        return ThreadWakeMode.OFF

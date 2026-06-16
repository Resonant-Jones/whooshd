"""ThreadWake Phase A eligibility policy."""

from __future__ import annotations

from .types import PromptGraph, ThreadWakeMode, ThreadWakeObservation, ThreadWakeRequestConfig


DEFAULT_MIN_STABLE_PREFIX_TOKENS = 1024


def evaluate_threadwake_policy(
    graph: PromptGraph | None,
    config: ThreadWakeRequestConfig,
) -> ThreadWakeObservation:
    """Evaluate observe-mode cacheability without touching KV state."""

    mode = config.mode or ThreadWakeMode.OFF
    scope = config.scope or "thread"
    enabled = bool(config.enabled) and mode != ThreadWakeMode.OFF
    min_tokens = (
        config.min_stable_prefix_tokens
        if config.min_stable_prefix_tokens is not None
        else DEFAULT_MIN_STABLE_PREFIX_TOKENS
    )

    if not enabled:
        return ThreadWakeObservation(
            enabled=False,
            mode=ThreadWakeMode.OFF,
            eligible=False,
            reason="threadwake_disabled",
            cache_scope=scope,
        )

    if graph is None:
        return ThreadWakeObservation(
            enabled=True,
            mode=mode,
            eligible=False,
            reason="prompt_graph_missing",
            cache_scope=scope,
        )

    base = {
        "enabled": True,
        "mode": mode,
        "stable_prefix_hash": graph.stable_prefix_hash,
        "stable_prefix_tokens": graph.stable_prefix_tokens,
        "dynamic_tokens": graph.dynamic_tokens,
        "cache_scope": scope,
    }

    if not graph.model_id:
        return ThreadWakeObservation(**base, eligible=False, reason="model_id_missing")
    if not graph.backend:
        return ThreadWakeObservation(**base, eligible=False, reason="backend_missing")
    if mode not in (ThreadWakeMode.OBSERVE, ThreadWakeMode.EPHEMERAL, ThreadWakeMode.SESSION):
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason=f"mode_not_supported: {mode.value}",
        )
    if any(segment.in_stable_prefix and segment.multimodal for segment in graph.segments):
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason="stable_prefix_contains_multimodal",
        )
    if graph.stable_prefix_tokens < min_tokens:
        return ThreadWakeObservation(
            **base,
            eligible=False,
            reason="stable_prefix_below_min_tokens",
        )

    return ThreadWakeObservation(
        **base,
        eligible=True,
        reason=None,
        estimated_prefill_reuse_tokens=graph.stable_prefix_tokens,
        cache_hit=False,
    )

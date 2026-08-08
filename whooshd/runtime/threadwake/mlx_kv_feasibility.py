"""ThreadWake MLX prompt-cache feasibility probe.

Answers: can Whoosh'd safely use MLX-LM prompt caching as a ThreadWake
backend KV reuse mechanism?

Result: feasible through the generic KV protocol, but locked behind an
explicit experimental flag until all gating criteria are proven.

MLX-LM 0.31.3 provides:
  - ``make_prompt_cache(model) -> List[Any]`` — creates a per-model cache
  - ``stream_generate(..., prompt_cache=cache)`` — uses the cache as a
    pre-filled KV prefix for token-based generation
  - ``trim_prompt_cache(cache, num_tokens)`` — trims a cache
  - ``save_prompt_cache / load_prompt_cache`` — disk persistence

The cache is a list of per-layer KV state objects that the model accepts
as ``cache=prompt_cache`` during inference.  Because the API is
token-based (``model(token_ids, cache=cache)``), it maps cleanly to
ThreadWake's generic ``prefill_to_kv(token_ids)`` /
``generate_from_kv(kv_handle, token_ids)`` protocol.

Blockers for production use:
  - Cache creation requires running actual inference (populating the
    cache with the model), which has a latency cost.
  - The cache is tied to the same model object — cross-request sharing
    requires the model to stay loaded and the cache to stay in memory.
  - Cache cloning may require deep copying per-layer state.
  - Multi-request concurrency safety has not been tested.
  - Cache objects contain opaque MLX state — they must never leak
    through public runtime surfaces.
"""

from __future__ import annotations

import logging
from typing import Any

from whooshd.config import _env_bool

logger = logging.getLogger(__name__)

# ── API probe ──────────────────────────────────────────────────────────────


def probe_mlx_prompt_cache_api() -> dict[str, Any]:
    """Probe whether MLX-LM prompt-cache API is available.

    Returns a structured dict describing what was found.
    Never raises — import failures are recorded as unavailable.
    """
    result: dict[str, Any] = {
        "available": False,
        "mlx_lm_version": None,
        "make_prompt_cache": False,
        "save_prompt_cache": False,
        "load_prompt_cache": False,
        "trim_prompt_cache": False,
        "generate_accepts_prompt_cache": False,
        "stream_generate_accepts_prompt_cache": False,
        "blockers": [],
    }

    try:
        import mlx_lm
        result["mlx_lm_version"] = getattr(mlx_lm, "__version__", "unknown")
    except Exception as exc:
        result["blockers"].append(f"mlx_lm unavailable ({type(exc).__name__})")
        return result

    try:
        from mlx_lm.models.cache import (
            make_prompt_cache,
            save_prompt_cache,
            load_prompt_cache,
            trim_prompt_cache,
        )
        result["make_prompt_cache"] = callable(make_prompt_cache)
        result["save_prompt_cache"] = callable(save_prompt_cache)
        result["load_prompt_cache"] = callable(load_prompt_cache)
        result["trim_prompt_cache"] = callable(trim_prompt_cache)
    except Exception as exc:
        result["blockers"].append(
            f"mlx_lm.models.cache unavailable ({type(exc).__name__})"
        )

    # Check if the generate module contains prompt_cache support.
    # prompt_cache travels through **kwargs in stream_generate, so we
    # scan the full module source for any function accepting it.
    try:
        import importlib
        gen_mod = importlib.import_module("mlx_lm.generate")
        src_path = getattr(gen_mod, "__file__", None)
        if src_path:
            with open(src_path) as f:
                has_pc = "prompt_cache" in f.read()
        else:
            import inspect
            has_pc = "prompt_cache" in inspect.getsource(gen_mod)
        result["stream_generate_accepts_prompt_cache"] = has_pc
        result["generate_accepts_prompt_cache"] = has_pc
    except Exception:
        result["blockers"].append("could not inspect mlx_lm.generate source")

    if not result["blockers"] and result["make_prompt_cache"] and result.get("stream_generate_accepts_prompt_cache"):
        result["available"] = True

    return result


def is_mlx_kv_feasible() -> bool:
    """Return True if MLX-LM prompt-cache API is available.

    This does NOT mean ThreadWake KV reuse is enabled — only that the
    API surface exists and the experimental adapter CAN attempt it.
    """
    return probe_mlx_prompt_cache_api().get("available", False)


# ── Experimental gate ──────────────────────────────────────────────────────


def get_mlx_kv_experimental_enabled() -> bool:
    """Whether experimental MLX KV reuse is enabled.

    Default: False.  Must be explicitly set to True for any attempt.
    """
    return _env_bool("WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL", False)


def get_mlx_kv_feasibility_report() -> str:
    """Human-readable feasibility report."""
    probe = probe_mlx_prompt_cache_api()
    if probe["available"]:
        return (
            f"MLX-LM {probe['mlx_lm_version']}: prompt-cache API available. "
            f"Experimental KV reuse is gated behind "
            f"WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true."
        )
    blockers = probe.get("blockers", [])
    return (
        f"MLX-LM prompt-cache API not available: {', '.join(blockers)}. "
        f"MLX KV reuse remains unsupported."
    )

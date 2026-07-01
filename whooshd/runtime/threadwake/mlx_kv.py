"""MLX KV backend skeleton for ThreadWake.

Defines the MLX KV adapter boundary.  Real MLX KV reuse is gated
behind an explicit experimental flag because cache creation requires
running model inference to populate the cache.

Feasibility probe result (mlx-lm 0.31.3):
  The MLX-LM prompt-cache API (``make_prompt_cache``,
  ``stream_generate(..., prompt_cache=...)``) is token-based and maps
  cleanly to ThreadWake's ``prefill_to_kv`` / ``generate_from_kv``
  protocol.  Cache creation requires running the model on the stable
  prefix tokens to populate the cache state.

Blockers before production capability:
  - Cache creation incurs inference latency.
  - The cache is tied to the same model object in memory.
  - Multi-request concurrency safety is untested.
  - Cache cloning requires deep-copying per-layer MLX state.

When ``WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true`` AND the MLX-LM
prompt-cache API is available, the adapter reports an experimental
capability.  By default, capability remains ``UNSUPPORTED``.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from .handles import KVCapability, KVHandle

logger = logging.getLogger(__name__)


class MLXKVBackendAdapter:
    """MLX KV adapter with experimental prompt-cache support.

    Reports ``KVCapability.UNSUPPORTED`` by default.

    When ``WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true`` and MLX-LM
    prompt-cache API is available, reports ``KVCapability.EXPERIMENTAL``
    and provides experimental prefill/generate implementations.

    ``clone_kv`` remains unimplemented until cache copying is proven safe.
    ``release_kv`` is always a safe no-op.
    """

    def __init__(self, model: Any = None, tokenizer: Any = None) -> None:
        self._model = model
        self._tokenizer = tokenizer

    def supports_kv_cache(self) -> KVCapability:
        """Report KV capability honestly.

        - ``UNSUPPORTED`` by default.
        - ``EXPERIMENTAL`` only when the experimental flag is set
          AND the MLX-LM prompt-cache API is available.
        """
        from .mlx_kv_feasibility import get_mlx_kv_experimental_enabled, is_mlx_kv_feasible

        if get_mlx_kv_experimental_enabled() and is_mlx_kv_feasible():
            return KVCapability.EXPERIMENTAL
        return KVCapability.UNSUPPORTED

    def prefill_to_kv(
        self,
        tokens: list[int] | list[list[int]],
        *,
        model_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KVHandle:
        """Experimental: populate MLX prompt cache from stable prefix tokens.

        Requires ``WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true`` and a
        loaded MLX model.  If the model is not loaded or the cache cannot
        be populated, raises ``RuntimeError``.
        """
        if self._model is None:
            raise RuntimeError(
                "MLX KV prefill: model not loaded — cannot populate prompt cache"
            )

        try:
            from mlx_lm.models.cache import make_prompt_cache
            import mlx.core as mx

            # Flatten token list if nested.
            if tokens and isinstance(tokens[0], list):
                flat_tokens = [t for sub in tokens for t in sub]
            else:
                flat_tokens = list(tokens)

            if not flat_tokens:
                raise RuntimeError(
                    "MLX KV prefill: no tokens provided — cannot populate cache"
                )

            # Create a prompt cache for this model.
            prompt_cache = make_prompt_cache(self._model)

            # Populate the cache by running the model on the tokens.
            token_array = mx.array([flat_tokens])
            self._model(token_array, cache=prompt_cache)
            mx.eval([c.state for c in prompt_cache])

            return KVHandle(
                backend="mlx",
                model_id=model_id,
                token_count=len(flat_tokens),
                opaque_ref={"prompt_cache": prompt_cache},
            )
        except ImportError:
            raise RuntimeError(
                "MLX KV prefill: mlx_lm.models.cache not available — "
                "prompt cache cannot be populated"
            )
        except Exception as exc:
            raise RuntimeError(
                f"MLX KV prefill failed: {exc}"
            ) from exc

    def generate_from_kv(
        self,
        kv_handle: KVHandle,
        new_tokens: list[int],
        generation_params: dict[str, Any],
    ) -> Iterator[str]:
        """Experimental: generate from MLX prompt cache with dynamic tokens.

        Requires a valid ``KVHandle`` with a populated ``prompt_cache``
        in its opaque reference.
        """
        if self._model is None or self._tokenizer is None:
            raise RuntimeError(
                "MLX KV generate: model or tokenizer not loaded"
            )

        opaque = kv_handle.opaque_ref
        if not isinstance(opaque, dict) or "prompt_cache" not in opaque:
            raise RuntimeError(
                "MLX KV generate: KV handle has no prompt_cache — "
                "was prefill_to_kv called successfully?"
            )

        from mlx_lm import stream_generate

        prompt_cache = opaque["prompt_cache"]
        max_tokens = generation_params.get("max_tokens", 256)

        for response in stream_generate(
            self._model,
            self._tokenizer,
            prompt=new_tokens,
            max_tokens=max_tokens,
            prompt_cache=prompt_cache,
        ):
            if response.text:
                yield response.text

    def clone_kv(self, kv_handle: KVHandle) -> KVHandle:
        """Not yet implemented — cache cloning requires deep-copying
        per-layer MLX state."""
        raise RuntimeError(
            "MLX KV clone is not implemented yet; "
            "use the standard full-prefill path"
        )

    def release_kv(self, kv_handle: KVHandle) -> None:
        """Release in-memory cache references."""
        opaque = kv_handle.opaque_ref
        if isinstance(opaque, dict):
            opaque.pop("prompt_cache", None)

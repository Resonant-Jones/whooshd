"""MLX KV backend skeleton for ThreadWake.

Defines the MLX KV adapter boundary without enabling real KV reuse yet.

This adapter intentionally reports unsupported until Whoosh'd has a proven
MLX prefill/resume path.  It exists to make the future integration boundary
explicit without making unsafe reuse claims.

When real MLX KV prefill/resume is implemented, this adapter will be
upgraded to report ``RESUMABLE`` or ``CLONEABLE`` and then wired into
actual ``mlx_lm`` KV capture / generation from KV.
"""

from __future__ import annotations

from typing import Any, Iterator

from .handles import KVCapability, KVHandle


class MLXKVBackendAdapter:
    """MLX KV adapter skeleton.

    Reports ``KVCapability.UNSUPPORTED`` until real MLX KV prefill/resume
    is implemented.  All unsafe operations raise ``RuntimeError`` with
    clear messages.  ``release_kv`` is a safe no-op.

    This adapter is the honest answer to "can MLX reuse KV cache yet?"
    The answer is "no" until proven otherwise.
    """

    def __init__(self, model: Any = None, tokenizer: Any = None) -> None:
        self._model = model
        self._tokenizer = tokenizer

    def supports_kv_cache(self) -> KVCapability:
        """MLX KV reuse is not implemented yet."""
        return KVCapability.UNSUPPORTED

    def prefill_to_kv(
        self,
        tokens: list[int] | list[list[int]],
        *,
        model_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KVHandle:
        raise RuntimeError(
            "MLX KV prefill is not implemented yet; "
            "use the standard full-prefill path"
        )

    def generate_from_kv(
        self,
        kv_handle: KVHandle,
        new_tokens: list[int],
        generation_params: dict[str, Any],
    ) -> Iterator[str]:
        raise RuntimeError(
            "MLX KV resume is not implemented yet; "
            "use the standard generation path"
        )

    def clone_kv(self, kv_handle: KVHandle) -> KVHandle:
        raise RuntimeError(
            "MLX KV clone is not implemented yet; "
            "use the standard full-prefill path"
        )

    def release_kv(self, kv_handle: KVHandle) -> None:
        """No-op — no resources held."""
        return None

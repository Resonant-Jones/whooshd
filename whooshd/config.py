"""Whoosh'd runtime configuration.

All settings are read from environment variables with safe defaults.
No config file parsing yet — that can come later when the surface area grows.
"""

from __future__ import annotations

import os


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default).lower()).lower() in ("1", "true", "yes")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


# ── Adapter selection ───────────────────────────────────────────────────────


def get_adapter_backend() -> str:
    """Return the configured adapter backend name.

    Values:
      * ``"stub"`` — deterministic stub (default, always available)
      * ``"mlx"``  — real mlx-lm inference (requires mlx-lm installed)
    """
    return _env("WHOOSHD_ADAPTER", "stub")


# ── MLX model settings ──────────────────────────────────────────────────────


def get_mlx_model_path() -> str:
    """HuggingFace repo id or local path for the MLX model."""
    return _env(
        "WHOOSHD_MLX_MODEL",
        "mlx-community/Llama-3.2-3B-Instruct-4bit",
    )


def get_mlx_max_tokens_default() -> int:
    """Default max_tokens when the request does not specify one."""
    return _env_int("WHOOSHD_MLX_MAX_TOKENS_DEFAULT", 256)


def get_mlx_trust_remote_code() -> bool:
    """Allow custom code in model repos.  Off by default."""
    return _env_bool("WHOOSHD_MLX_TRUST_REMOTE_CODE", False)

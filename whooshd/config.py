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


# ── Admission control ───────────────────────────────────────────────────────


def get_max_active_requests() -> int:
    """Maximum concurrent active requests before rejecting with 429.

    Default 2 to match Codexify's current chat worker concurrency.
    """
    return _env_int("WHOOSHD_MAX_ACTIVE_REQUESTS", 2)


def get_max_prompt_chars() -> int:
    """Maximum estimated prompt character count before rejection."""
    return _env_int("WHOOSHD_MAX_PROMPT_CHARS", 262144)


def get_max_messages() -> int:
    """Maximum number of messages in a chat request."""
    return _env_int("WHOOSHD_MAX_MESSAGES", 128)


def get_max_request_max_tokens() -> int:
    """Server-side cap on max_tokens, even if the contract allows more."""
    return _env_int("WHOOSHD_MAX_REQUEST_MAX_TOKENS", 32768)

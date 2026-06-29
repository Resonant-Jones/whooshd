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


def get_advertised_model_id() -> str:
    """Return the model id that inventory endpoints should advertise.

    Stub mode advertises ``stub-model``. MLX mode advertises the exact
    configured ``WHOOSHD_MLX_MODEL`` so Codexify can validate the pin
    without loosening its inventory gate.
    """
    if get_adapter_backend().strip().lower() == "mlx":
        configured = get_mlx_model_path().strip()
        if configured:
            return configured
    return "stub-model"


def get_mlx_max_tokens_default() -> int:
    """Default max_tokens when the request does not specify one."""
    return _env_int("WHOOSHD_MLX_MAX_TOKENS_DEFAULT", 256)


def get_mlx_trust_remote_code() -> bool:
    """Allow custom code in model repos.  Off by default."""
    return _env_bool("WHOOSHD_MLX_TRUST_REMOTE_CODE", False)


def get_mlx_context_window() -> int:
    """Model context window in tokens.

    Optional override — when unset, Whoosh'd tries to read the value
    from the model's config.json at startup.  Falls back to 32768.
    """
    return _env_int("WHOOSHD_MLX_CONTEXT_WINDOW", 0)


def get_mlx_quantization() -> str | None:
    """Human-readable quantization label (e.g. '3bit-mixed', '4bit').

    Optional override — when unset, Whoosh'd tries to detect it from
    config.json or the model directory name.
    """
    val = _env("WHOOSHD_MLX_QUANTIZATION", "")
    return val if val else None


# ── Queue config ─────────────────────────────────────────────────────────────


def get_enable_queue() -> bool:
    """Enable optional bounded FIFO request queue.

    When false (default), the current reject-only behaviour is preserved:
    requests at the active limit receive 429 immediately.
    """
    return _env_bool("WHOOSHD_ENABLE_QUEUE", False)


def get_max_queue_depth() -> int:
    """Maximum number of requests allowed in the queue."""
    return _env_int("WHOOSHD_MAX_QUEUE_DEPTH", 8)


def get_queue_timeout_seconds() -> float:
    """Max seconds a request waits in the queue before timing out."""
    try:
        return float(_env("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "120"))
    except ValueError:
        return 120.0


def get_queue_poll_interval_ms() -> int:
    """How often (ms) to check for capacity while queued."""
    return _env_int("WHOOSHD_QUEUE_POLL_INTERVAL_MS", 25)


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


# ── llama.cpp settings ───────────────────────────────────────────────────


def get_llama_cpp_server_url() -> str | None:
    """Base URL of an existing llama.cpp server (e.g. http://127.0.0.1:8080).

    When set, the llama.cpp adapter probes this server for health.
    When unset, the adapter reports offline/no-server state.
    """
    val = _env("WHOOSHD_LLAMA_CPP_SERVER_URL", "")
    return val if val else None


def get_llama_cpp_binary_path() -> str | None:
    """Path to the llama-server binary (for future managed mode).

    Not used in the current scaffolding phase.
    """
    val = _env("WHOOSHD_LLAMA_CPP_BINARY_PATH", "")
    return val if val else None


def get_llama_cpp_host() -> str:
    """Host for a future locally-managed llama.cpp server."""
    return _env("WHOOSHD_LLAMA_CPP_HOST", "127.0.0.1")


def get_llama_cpp_port() -> int:
    """Port for a future locally-managed llama.cpp server."""
    return _env_int("WHOOSHD_LLAMA_CPP_PORT", 8080)


def get_llama_cpp_auto_start() -> bool:
    """Whether to auto-start a managed llama.cpp server.  Off by default."""
    return _env_bool("WHOOSHD_LLAMA_CPP_AUTO_START", False)


def get_llama_cpp_startup_timeout_seconds() -> float:
    """Max seconds to wait for a managed llama.cpp server to start."""
    try:
        return float(_env("WHOOSHD_LLAMA_CPP_STARTUP_TIMEOUT_SECONDS", "30.0"))
    except ValueError:
        return 30.0


def get_llama_cpp_health_timeout_seconds() -> float:
    """Max seconds for a llama.cpp health probe HTTP request."""
    try:
        return float(_env("WHOOSHD_LLAMA_CPP_HEALTH_TIMEOUT_SECONDS", "2.0"))
    except ValueError:
        return 2.0


def get_llama_cpp_model_path() -> str | None:
    """Path to the GGUF model file for managed llama.cpp mode.

    Required when ``auto_start=true``.
    """
    val = _env("WHOOSHD_LLAMA_CPP_MODEL_PATH", "")
    return val if val else None


# ── MLX-LM Server settings ──────────────────────────────────────────────


def get_mlx_lm_server_enabled() -> bool:
    """Whether the MLX-LM Server runtime lane is enabled."""
    return _env_bool("WHOOSHD_MLX_ENABLED", False)


def get_mlx_lm_server_host() -> str:
    """Host for the supervised mlx_lm.server process."""
    return _env("WHOOSHD_MLX_HOST", "127.0.0.1")


def get_mlx_lm_server_port() -> int:
    """Port for the supervised mlx_lm.server process."""
    return _env_int("WHOOSHD_MLX_PORT", 8081)


def get_mlx_lm_server_model() -> str | None:
    """HF repo id or local path for the MLX-LM Server model.

    When unset, the MLX-LM Server runtime is effectively disabled
    (no model to serve).
    """
    val = _env("WHOOSHD_MLX_MODEL", "")
    return val if val else None


def get_mlx_lm_server_extra_args() -> list[str]:
    """Extra CLI arguments for mlx_lm.server."""
    val = _env("WHOOSHD_MLX_EXTRA_ARGS", "")
    if val:
        return val.split()
    return []


def get_mlx_lm_server_startup_timeout_seconds() -> float:
    """Max seconds to wait for mlx_lm.server to start."""
    try:
        return float(_env("WHOOSHD_MLX_STARTUP_TIMEOUT_SECONDS", "30.0"))
    except ValueError:
        return 30.0


def get_mlx_lm_server_health_timeout_seconds() -> float:
    """Max seconds for an mlx_lm.server health probe HTTP request."""
    try:
        return float(_env("WHOOSHD_MLX_HEALTH_TIMEOUT_SECONDS", "2.0"))
    except ValueError:
        return 2.0


# ── Model registry ─────────────────────────────────────────────────────────


def get_model_registry_path() -> str | None:
    """Return the explicit model registry YAML path, or None.

    When set, Whoosh'd loads the model registry from this file.
    When unset, the default search path is used (configs/models.yaml,
    whooshd/config/models.yaml).  If no registry file is found at all,
    Whoosh'd falls back to the single-model environment-variable behaviour.
    """
    val = _env("WHOOSHD_MODEL_REGISTRY_PATH", "")
    return val if val else None


# ── Runtime concurrency guardrails ──────────────────────────────────────────


def get_mlx_max_concurrent_requests() -> int:
    """Maximum concurrent requests the MLX-LM Server runtime will accept.

    Requests beyond this limit are rejected with 429 immediately
    rather than hanging.
    """
    return _env_int("WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS", 2)


def get_mlx_vlm_max_concurrent_requests() -> int:
    """Maximum concurrent requests the MLX-VLM runtime will accept.

    Vision models are typically larger — default to 1.
    """
    return _env_int("WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS", 1)


def get_llama_cpp_max_concurrent_requests() -> int:
    """Maximum concurrent requests the llama.cpp runtime will accept."""
    return _env_int("WHOOSHD_LLAMA_CPP_MAX_CONCURRENT_REQUESTS", 2)


def get_runtime_acquire_timeout_seconds() -> float:
    """Max seconds to wait for a runtime concurrency slot.

    If the slot cannot be acquired within this timeout, the request
    is rejected with 429.
    """
    try:
        return float(_env("WHOOSHD_RUNTIME_ACQUIRE_TIMEOUT_SECONDS", "5.0"))
    except ValueError:
        return 5.0


# ── ThreadWake observe-mode settings ──────────────────────────────────────


def get_threadwake_enabled() -> bool:
    """Whether ThreadWake observe-mode analysis is enabled by default."""
    return _env_bool("WHOOSHD_THREADWAKE_ENABLED", False)


def get_threadwake_mode() -> str:
    """Default ThreadWake mode. Phase A only implements ``observe``."""
    return _env("WHOOSHD_THREADWAKE_MODE", "off")


def get_threadwake_min_prefix_tokens() -> int:
    """Minimum stable prefix token estimate for ThreadWake eligibility."""
    return _env_int("WHOOSHD_THREADWAKE_MIN_PREFIX_TOKENS", 1024)


def get_threadwake_default_scope() -> str:
    """Default ThreadWake cacheability scope for observations."""
    val = _env("WHOOSHD_THREADWAKE_DEFAULT_SCOPE", "thread")
    if val in {"request", "thread", "project", "user", "global"}:
        return val
    return "thread"


def get_threadwake_max_entries() -> int:
    """Maximum number of entries in the ThreadWake metadata index."""
    return _env_int("WHOOSHD_THREADWAKE_MAX_ENTRIES", 16)


def get_threadwake_max_memory_mb() -> int:
    """Maximum estimated memory (MiB) for the ThreadWake metadata index."""
    return _env_int("WHOOSHD_THREADWAKE_MAX_MEMORY_MB", 1024)


def get_threadwake_bytes_per_token() -> int:
    """Estimated bytes per token for KV cache memory modelling.

    Set to 0 to disable memory-based eviction entirely.
    """
    return _env_int("WHOOSHD_THREADWAKE_BYTES_PER_TOKEN", 0)


def get_threadwake_allow_global() -> bool:
    """Whether global-scope ThreadWake cache is permitted.

    Must be explicitly enabled.  Disabled by default for safety.
    """
    return _env_bool("WHOOSHD_THREADWAKE_ALLOW_GLOBAL", False)


def get_threadwake_mlx_tokenizer_enabled() -> bool:
    """Whether to register the MLX tokenizer adapter with ThreadWake.

    When enabled and MLX is the active backend, the loaded tokenizer
    is registered so ThreadWake can use real token IDs for observations.
    This does NOT enable production KV reuse.
    """
    return _env_bool("WHOOSHD_THREADWAKE_MLX_TOKENIZER_ENABLED", False)


def get_threadwake_mlx_kv_reuse_enabled() -> bool:
    """Placeholder gate for future MLX KV reuse.  Always false for now."""
    return False  # Hard-disabled; not controlled by env


def get_threadwake_sqlite_enabled() -> bool:
    """Whether to persist ThreadWake candidate telemetry to SQLite."""
    return _env_bool("WHOOSHD_THREADWAKE_SQLITE_ENABLED", False)


def get_threadwake_sqlite_path() -> str:
    """Path to the ThreadWake SQLite database file."""
    return _env("WHOOSHD_THREADWAKE_SQLITE_PATH", ".whooshd/threadwake.sqlite3")


def get_threadwake_experimental_snapshots_enabled() -> bool:
    """Enable experimental snapshot creation.  Disabled by default."""
    return _env_bool("WHOOSHD_THREADWAKE_EXPERIMENTAL_SNAPSHOTS_ENABLED", False)


# ── MLX-VLM settings ──────────────────────────────────────────────────────


def get_mlx_vlm_enabled() -> bool:
    """Whether the MLX-VLM vision runtime lane is enabled."""
    return _env_bool("WHOOSHD_MLX_VLM_ENABLED", False)


def get_mlx_vlm_model() -> str | None:
    """HF repo id or local path for the MLX-VLM model."""
    val = _env("WHOOSHD_MLX_VLM_MODEL", "")
    return val if val else None


def get_mlx_vlm_host() -> str:
    """Host for the supervised mlx-vlm server process."""
    return _env("WHOOSHD_MLX_VLM_HOST", "127.0.0.1")


def get_mlx_vlm_port() -> int:
    """Port for the supervised mlx-vlm server process."""
    return _env_int("WHOOSHD_MLX_VLM_PORT", 8082)


def get_mlx_vlm_extra_args() -> list[str]:
    """Extra CLI arguments for mlx-vlm server."""
    val = _env("WHOOSHD_MLX_VLM_EXTRA_ARGS", "")
    if val:
        return val.split()
    return []


def get_mlx_vlm_startup_timeout_seconds() -> float:
    """Max seconds to wait for mlx-vlm server to start."""
    try:
        return float(_env("WHOOSHD_MLX_VLM_STARTUP_TIMEOUT_SECONDS", "60.0"))
    except ValueError:
        return 60.0


def get_mlx_vlm_health_timeout_seconds() -> float:
    """Max seconds for an mlx-vlm health probe HTTP request."""
    try:
        return float(_env("WHOOSHD_MLX_VLM_HEALTH_TIMEOUT_SECONDS", "2.0"))
    except ValueError:
        return 2.0


# ── Model-store settings ──────────────────────────────────────────────────


def get_model_store_root() -> str | None:
    """Root path for the persistent Whoosh'd model-store.

    When set, Whoosh'd can discover and advertise compatible registered
    models from ``registry/models.json`` in addition to built-in/static
    model inventory.  When unset, only built-in/static models are advertised.
    """
    val = _env("WHOOSHD_MODEL_STORE_ROOT", "")
    return val if val else None

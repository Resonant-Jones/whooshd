"""Deterministic hash and key helpers for ThreadWake Phase A."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .types import PromptGraph, ThreadWakeScope


THREADWAKE_KEY_VERSION = "threadwake-v0-phase-a"


def _normalize_text(value: str) -> str:
    """Normalize line endings without trimming meaningful whitespace."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_value(value: Any) -> Any:
    """Normalize nested JSON-like values for deterministic hashing."""
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value)}
    return value


def canonicalize_content(content: Any) -> str:
    """Canonicalize message/tool content without dropping semantic data."""
    normalized = _normalize_value(content)
    if isinstance(normalized, str):
        return normalized
    return canonical_json(normalized)


def canonical_json(value: Any) -> str:
    """Return stable JSON for hashing.

    ``ensure_ascii=False`` preserves semantic text before hashing while the
    hash output remains ASCII. Callers must not log this canonical payload.
    """

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: str | bytes) -> str:
    """Return a SHA-256 hex digest for text or bytes."""

    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def hash_json(value: Any) -> str:
    """Hash a JSON-serialisable value with deterministic key ordering."""

    return sha256_hex(canonical_json(value))


def build_threadwake_cache_key(
    graph: PromptGraph,
    *,
    scope: ThreadWakeScope = "thread",
    model_revision: str | None = None,
    quantization: str | None = None,
    version: str = THREADWAKE_KEY_VERSION,
) -> str:
    """Build a deterministic Phase A cache key.

    Phase A only generates this key for observability/tests. It must not be
    used to retrieve or store KV cache blocks.
    """

    return hash_json(
        {
            "version": version,
            "backend": graph.backend,
            "model_id": graph.model_id,
            "model_revision": model_revision,
            "quantization": quantization,
            "tokenizer_hash": graph.tokenizer_hash,
            "chat_template_hash": graph.chat_template_hash,
            "stable_prefix_hash": graph.stable_prefix_hash,
            "scope": scope,
        }
    )

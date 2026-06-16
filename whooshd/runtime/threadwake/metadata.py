"""Codexify segment metadata validation and merging for ThreadWake Phase F.

Validates optional ``threadwake_segments`` metadata from Codexify requests
against actual message content.  Merges valid metadata into inferred
PromptSegments, and degrades invalid entries to inference-only.
"""

from __future__ import annotations

from typing import Any

from .keys import canonicalize_content, sha256_hex
from .types import (
    CodexifySegmentMeta,
    CodexifySegmentMetadata,
    PromptSegment,
    ThreadWakeScope,
)


# ── Default stability / type mapping per Codexify segment type ─────────────


_CODEXIFY_TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "system":      {"stability": "stable", "segment_type": "system"},
    "persona":     {"stability": "stable", "segment_type": "persona"},
    "tools":       {"stability": "stable", "segment_type": "tool_schema"},
    "project":     {"stability": "stable", "segment_type": "project_context"},
    "retrieval":   {"stability": "semi_stable", "segment_type": "retrieval"},
    "thread":      {"stability": "semi_stable", "segment_type": "thread_history"},
    "user":        {"stability": "dynamic", "segment_type": "user_message"},
    "tool_output": {"stability": "dynamic", "segment_type": "tool_output"},
    "unknown":     {"stability": "dynamic", "segment_type": "unknown"},
}


def _is_segment_type_dynamic(seg_type: str) -> bool:
    """Return True for segment types that are always dynamic."""
    return seg_type in ("tool_output",)


def _is_segment_type_cacheable(seg_type: str) -> bool:
    """Return True for segment types that can be in the stable prefix."""
    return seg_type not in ("tool_output", "user")


# ── Validation ─────────────────────────────────────────────────────────────


class MetadataValidationError(Exception):
    """Raised when metadata validation fails in a non-degradable way."""


def validate_and_merge_segments(
    *,
    inferred_segments: list[PromptSegment],
    codexify_metadata: CodexifySegmentMetadata | None,
    messages: list[Any],
    default_scope: ThreadWakeScope = "thread",
    allow_global: bool = False,
) -> tuple[list[PromptSegment], list[str]]:
    """Validate Codexify metadata and merge into inferred segments.

    Returns ``(merged_segments, errors)``.  If metadata is None or
    empty, returns the inferred segments unchanged.  Invalid entries
    are skipped with an error message; the inferred segment is kept.
    """
    if codexify_metadata is None or not codexify_metadata.segments:
        return inferred_segments, []

    errors: list[str] = []
    meta_by_index: dict[int, CodexifySegmentMeta] = {}
    for meta in codexify_metadata.segments:
        idx = meta.message_index
        if idx in meta_by_index:
            errors.append(f"duplicate_message_index: {idx}")
            continue
        meta_by_index[idx] = meta

    merged: list[PromptSegment] = []

    for i, seg in enumerate(inferred_segments):
        meta = meta_by_index.get(i)
        if meta is None:
            # No metadata for this segment — keep inferred
            merged.append(seg)
            continue

        error = _validate_single_meta(meta, seg, messages, allow_global)
        if error:
            errors.append(f"segment[{i}]: {error}")
            merged.append(seg)  # Fall back to inferred
            continue

        # Merge valid metadata into the segment
        merged_seg = _apply_meta(meta, seg, default_scope)
        merged.append(merged_seg)

    return merged, errors


def _validate_single_meta(
    meta: CodexifySegmentMeta,
    seg: PromptSegment,
    messages: list[Any],
    allow_global: bool,
) -> str | None:
    """Return an error string if metadata is invalid, or None if valid."""
    # message_index bounds check
    if meta.message_index < 0:
        return "invalid_message_index: negative"
    if meta.message_index >= len(messages):
        return f"message_index_out_of_range: {meta.message_index} >= {len(messages)}"

    # content_hash validation
    if meta.content_hash:
        # Recompute from actual message content
        msg = messages[meta.message_index]
        content = _extract_message_content(msg)
        canonical = canonicalize_content(content)
        computed_hash = sha256_hex(canonical)
        if computed_hash != meta.content_hash:
            return (
                f"content_hash_mismatch: "
                f"provided={meta.content_hash[:12]}... "
                f"computed={computed_hash[:12]}..."
            )

    # Global scope validation
    if meta.scope == "global" and not allow_global:
        return "global_scope_not_allowed"

    return None


def _apply_meta(
    meta: CodexifySegmentMeta,
    seg: PromptSegment,
    default_scope: ThreadWakeScope,
) -> PromptSegment:
    """Apply validated Codexify metadata to a PromptSegment."""
    type_defaults = _CODEXIFY_TYPE_DEFAULTS.get(meta.segment_type, {})

    # Stability: use metadata if explicitly provided, else type default, else inferred
    stability = (
        meta.stability
        if meta.stability is not None
        else type_defaults.get("stability", seg.stability)
    )

    # Segment type: use Codexify type mapping
    seg_type = type_defaults.get("segment_type", seg.segment_type)
    if meta.segment_type == "user":
        stability = "dynamic"  # User messages are always dynamic
        seg_type = "user_message"
    elif meta.segment_type == "tool_output":
        stability = "dynamic"  # Tool outputs are always dynamic
        seg_type = "tool_output"
    elif meta.segment_type == "retrieval":
        # Retrieval defaults to semi_stable but can be overridden to stable
        # Only trust stable if content_hash was validated
        if stability == "stable" and not meta.content_hash:
            stability = "semi_stable"  # Don't trust without hash validation

    # Scope
    scope: ThreadWakeScope = meta.scope or default_scope  # type: ignore[assignment]

    # Cacheable: if explicitly False, exclude from stable prefix
    cacheable = meta.cacheable
    if cacheable is False:
        stability = "dynamic"

    # Build merged segment
    return PromptSegment(
        name=meta.name or seg.name,
        role=seg.role,
        content_hash=seg.content_hash,
        segment_type=seg_type,
        stability=stability,  # type: ignore[arg-type]
        token_count=seg.token_count,
        scope=scope,
        multimodal=seg.multimodal,
        in_stable_prefix=False,  # Will be recomputed by compiler
    )


def _extract_message_content(msg: Any) -> Any:
    """Extract content from a message dict or object."""
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def parse_codexify_segments(raw: Any) -> CodexifySegmentMetadata | None:
    """Parse raw threadwake_segments from request body into typed metadata.

    Returns None if the field is missing, empty, or malformed.
    """
    if raw is None:
        return None
    if isinstance(raw, CodexifySegmentMetadata):
        return raw
    if isinstance(raw, dict):
        try:
            return CodexifySegmentMetadata.model_validate(raw)
        except Exception:
            return None
    if isinstance(raw, list):
        try:
            return CodexifySegmentMetadata(segments=[
                CodexifySegmentMeta.model_validate(s) for s in raw
            ])
        except Exception:
            return None
    return None

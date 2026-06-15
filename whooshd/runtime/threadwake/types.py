"""ThreadWake observe-mode data contracts.

These models deliberately exclude raw prompt content. ThreadWake Phase A is an
observability foundation only: it reports deterministic hashes and token
estimates, never reusable KV state.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ThreadWakeMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    EPHEMERAL = "ephemeral"
    SESSION = "session"
    ADVANCED = "advanced"


ThreadWakeScope = Literal["request", "thread", "project", "user", "global"]


class PromptSegment(BaseModel):
    """A hashed prompt segment with no raw prompt content."""

    name: str
    role: str
    content_hash: str
    segment_type: str
    stability: Literal["stable", "semi_stable", "dynamic"]
    token_count: int = Field(0, ge=0)
    scope: ThreadWakeScope = "thread"
    multimodal: bool = False
    in_stable_prefix: bool = False


class PromptGraph(BaseModel):
    """Deterministic prompt graph used for cacheability observations."""

    model_id: Optional[str] = None
    backend: Optional[str] = None
    chat_template_hash: Optional[str] = None
    tokenizer_hash: Optional[str] = None
    segments: list[PromptSegment] = Field(default_factory=list)
    stable_prefix_hash: str
    full_prompt_hash: str
    stable_prefix_tokens: int = Field(0, ge=0)
    dynamic_tokens: int = Field(0, ge=0)


class ThreadWakeRequestConfig(BaseModel):
    """Resolved request config for ThreadWake observe-mode analysis."""

    enabled: Optional[bool] = None
    mode: Optional[ThreadWakeMode] = None
    scope: Optional[ThreadWakeScope] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    min_stable_prefix_tokens: Optional[int] = Field(None, ge=0)


class ThreadWakeObservation(BaseModel):
    """Public-safe ThreadWake observation.

    The fields are safe for logs, metrics, and optional future metadata
    channels because they contain hashes and counts only.
    """

    enabled: bool
    mode: ThreadWakeMode
    eligible: bool
    reason: Optional[str] = None
    stable_prefix_hash: Optional[str] = None
    stable_prefix_tokens: int = Field(0, ge=0)
    dynamic_tokens: int = Field(0, ge=0)
    estimated_prefill_reuse_tokens: int = Field(0, ge=0)
    cache_hit: bool = False
    cache_scope: ThreadWakeScope = "thread"
    backend_kv_capability: Optional[str] = None
    can_reuse_kv: bool = False
    kv_reuse_reason: Optional[str] = None


class ThreadWakeMetadata(BaseModel):
    """Safe metadata attached to chat completion responses.

    Suitable for embedding in response metadata channels.
    """

    cache_hit: bool = False
    matched_tokens: int = Field(0, ge=0)
    mode: Optional[str] = None
    scope: Optional[str] = None
    backend_kv_capability: Optional[str] = None


class EphemeralResult(BaseModel):
    """Result of an ephemeral KV reuse execution.

    Contains generated output tokens and metadata about the execution path.
    """

    output_tokens: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    matched_tokens: int = Field(0, ge=0)
    observation: Optional[ThreadWakeObservation] = None
    metadata: Optional[ThreadWakeMetadata] = None

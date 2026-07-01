"""KV handle data model for ThreadWake Phase B.

KVHandle wraps backend-private KV state in a serialisation-safe envelope.
The ``opaque_ref`` field is explicitly excluded from public serialisation
and must never be sent to external consumers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KVCapability(str, Enum):
    """Backend KV cache capability level.

    Values are ordered from least to most capable. A backend may report any
    level; callers SHOULD treat the reported capability as an upper bound and
    MUST NOT assume a ``cloneable`` backend is also ``serializable``.
    """

    UNSUPPORTED = "unsupported"
    EXPERIMENTAL = "experimental"
    PREFILL_ONLY = "prefill_only"
    RESUMABLE = "resumable"
    CLONEABLE = "cloneable"
    SERIALIZABLE = "serializable"


class KVHandle(BaseModel):
    """Safe wrapper around backend-private KV state.

    Public fields are suitable for observability and routing. The
    ``opaque_ref`` field MUST NOT appear in logs, metrics, or API
    responses.
    """

    id: str = Field(default_factory=lambda: f"kv-{uuid.uuid4().hex[:12]}")
    backend: str
    model_id: str
    token_count: int = Field(0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scope: str = "thread"
    metadata: dict[str, Any] = Field(default_factory=dict)

    # --- Backend-private ---

    opaque_ref: Any = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Backend-private KV state reference; never serialised.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def touch(self) -> None:
        """Update last_used_at to now."""
        self.last_used_at = datetime.now(timezone.utc)

    def public_snapshot(self) -> dict[str, Any]:
        """Return a dict safe for external consumers (no opaque_ref)."""
        return self.model_dump(exclude={"opaque_ref"})

"""KV lifecycle observability for ThreadWake Phase M5.

Provides a thread-safe in-memory ring buffer for recording KV-related
events.  All identifiers are hashed before storage.  No raw prompts,
token IDs, opaque refs, or raw user/thread identifiers are stored.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# ── Event types ─────────────────────────────────────────────────────────────

KVEventType = Literal[
    "capability_reported",
    "kv_created",
    "kv_reused",
    "kv_cloned",
    "kv_released",
    "kv_invalidated",
    "kv_evicted",
    "kv_error",
    "generation_started",
    "generation_completed",
    "generation_cancelled",
]


def _hash_id(value: str | None) -> str | None:
    """SHA-256 hash an identifier for safe storage."""
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


# ── KV Event ────────────────────────────────────────────────────────────────


@dataclass
class KVEvent:
    """A single KV lifecycle event.  All identifiers are hashed.

    No raw prompts, token IDs, opaque refs, or raw scope IDs are stored.
    """

    event_id: str
    event_type: KVEventType
    backend: str | None = None
    model_id: str | None = None
    capability: str | None = None
    request_id_hash: str | None = None
    thread_id_hash: str | None = None
    cache_key_hash: str | None = None
    kv_handle_id_hash: str | None = None
    token_start: int | None = None
    token_end: int | None = None
    token_count: int | None = None
    reason: str | None = None
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        event_type: KVEventType,
        *,
        backend: str | None = None,
        model_id: str | None = None,
        capability: str | None = None,
        request_id: str | None = None,
        thread_id: str | None = None,
        cache_key: str | None = None,
        kv_handle_id: str | None = None,
        token_start: int | None = None,
        token_end: int | None = None,
        token_count: int | None = None,
        reason: str | None = None,
    ) -> KVEvent:
        import uuid
        return cls(
            event_id=uuid.uuid4().hex[:12],
            event_type=event_type,
            backend=backend,
            model_id=model_id,
            capability=capability,
            request_id_hash=_hash_id(request_id),
            thread_id_hash=_hash_id(thread_id),
            cache_key_hash=_hash_id(cache_key),
            kv_handle_id_hash=_hash_id(kv_handle_id),
            token_start=token_start,
            token_end=token_end,
            token_count=token_count,
            reason=reason,
        )

    def safe_dict(self) -> dict[str, Any]:
        """Return a dict safe for external consumers."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "backend": self.backend,
            "model_id": self.model_id,
            "capability": self.capability,
            "request_id_hash": self.request_id_hash,
            "thread_id_hash": self.thread_id_hash,
            "cache_key_hash": self.cache_key_hash,
            "kv_handle_id_hash": self.kv_handle_id_hash,
            "token_start": self.token_start,
            "token_end": self.token_end,
            "token_count": self.token_count,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


# ── Stats ───────────────────────────────────────────────────────────────────


@dataclass
class KVLifecycleStats:
    """Aggregate KV lifecycle statistics."""

    events_total: int = 0
    events_by_type: dict[str, int] = field(default_factory=dict)
    events_by_backend: dict[str, int] = field(default_factory=dict)
    active_handles_estimate: int = 0
    created_total: int = 0
    cloned_total: int = 0
    reused_total: int = 0
    released_total: int = 0
    invalidated_total: int = 0
    evicted_total: int = 0
    errors_total: int = 0
    last_event_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_total": self.events_total,
            "events_by_type": dict(self.events_by_type),
            "events_by_backend": dict(self.events_by_backend),
            "active_handles_estimate": self.active_handles_estimate,
            "created_total": self.created_total,
            "cloned_total": self.cloned_total,
            "reused_total": self.reused_total,
            "released_total": self.released_total,
            "invalidated_total": self.invalidated_total,
            "evicted_total": self.evicted_total,
            "errors_total": self.errors_total,
            "last_event_at": self.last_event_at,
        }


# ── Observer ────────────────────────────────────────────────────────────────


class KVLifecycleObserver:
    """Thread-safe ring buffer for KV lifecycle events.

    Stores events in a bounded circular buffer.  When the buffer is
    full, oldest events are evicted.  All identifiers are hashed
    before storage.

    If ``enabled=False``, all recording methods are safe no-ops.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_events: int = 512,
    ) -> None:
        self.enabled = enabled
        self._max_events = max(max_events, 1)
        self._lock = threading.Lock()
        self._events: list[KVEvent] = []
        self._cursor = 0
        self._active_handles = 0
        self._stats = KVLifecycleStats()

    # ── Recording ───────────────────────────────────────────────────────

    def record(self, event: KVEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._events) < self._max_events:
                self._events.append(event)
            else:
                self._events[self._cursor % self._max_events] = event
            self._cursor += 1
            self._update_stats(event)

    def _update_stats(self, event: KVEvent) -> None:
        s = self._stats
        s.events_total += 1
        etype = event.event_type
        s.events_by_type[etype] = s.events_by_type.get(etype, 0) + 1
        if event.backend:
            s.events_by_backend[event.backend] = s.events_by_backend.get(event.backend, 0) + 1
        s.last_event_at = event.timestamp

        if etype == "kv_created":
            s.created_total += 1
            self._active_handles += 1
        elif etype == "kv_cloned":
            s.cloned_total += 1
            self._active_handles += 1
        elif etype == "kv_reused":
            s.reused_total += 1
        elif etype == "kv_released":
            s.released_total += 1
            self._active_handles = max(0, self._active_handles - 1)
        elif etype == "kv_invalidated":
            s.invalidated_total += 1
        elif etype == "kv_evicted":
            s.evicted_total += 1
            self._active_handles = max(0, self._active_handles - 1)
        elif etype == "kv_error":
            s.errors_total += 1

        s.active_handles_estimate = self._active_handles

    # ── Convenience recorders ───────────────────────────────────────────

    def record_capability(
        self, *, backend: str, capability: str, model_id: str | None = None,
    ) -> None:
        self.record(KVEvent.create("capability_reported", backend=backend, capability=capability, model_id=model_id))

    def record_created(
        self, *, backend: str, model_id: str, token_count: int | None = None,
        kv_handle_id: str | None = None, cache_key: str | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_created", backend=backend, model_id=model_id, token_count=token_count, kv_handle_id=kv_handle_id, cache_key=cache_key))

    def record_cloned(
        self, *, backend: str, model_id: str, kv_handle_id: str | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_cloned", backend=backend, model_id=model_id, kv_handle_id=kv_handle_id))

    def record_reused(
        self, *, backend: str, model_id: str, token_count: int | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_reused", backend=backend, model_id=model_id, token_count=token_count))

    def record_released(
        self, *, backend: str, model_id: str, kv_handle_id: str | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_released", backend=backend, model_id=model_id, kv_handle_id=kv_handle_id))

    def record_invalidated(
        self, *, backend: str, model_id: str, reason: str | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_invalidated", backend=backend, model_id=model_id, reason=reason))

    def record_evicted(
        self, *, backend: str, model_id: str, reason: str | None = None,
    ) -> None:
        self.record(KVEvent.create("kv_evicted", backend=backend, model_id=model_id, reason=reason))

    def record_error(
        self, *, backend: str, model_id: str, reason: str, event_type: KVEventType = "kv_error",
    ) -> None:
        self.record(KVEvent.create(event_type, backend=backend, model_id=model_id, reason=reason))

    # ── Query ───────────────────────────────────────────────────────────

    def list_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
        backend: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        # Filter
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if backend:
            events = [e for e in events if e.backend == backend]
        # Most recent first
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return [e.safe_dict() for e in events[:limit]]

    def stats(self) -> KVLifecycleStats:
        with self._lock:
            # Return a copy so callers can't mutate internal state
            s = self._stats
            return KVLifecycleStats(
                events_total=s.events_total,
                events_by_type=dict(s.events_by_type),
                events_by_backend=dict(s.events_by_backend),
                active_handles_estimate=s.active_handles_estimate,
                created_total=s.created_total,
                cloned_total=s.cloned_total,
                reused_total=s.reused_total,
                released_total=s.released_total,
                invalidated_total=s.invalidated_total,
                evicted_total=s.evicted_total,
                errors_total=s.errors_total,
                last_event_at=s.last_event_at,
            )

    def clear(self) -> int:
        with self._lock:
            count = len(self._events)
            self._events.clear()
            self._cursor = 0
            self._active_handles = 0
            self._stats = KVLifecycleStats()
            return count

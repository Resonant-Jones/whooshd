"""ThreadWake in-memory cache metadata index.

Tracks candidate cache entries, lifecycle state, LRU eviction metadata,
cache scopes, and estimated memory usage.  No KV state is stored — this
is a metadata index only.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from .keys import sha256_hex

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────


class EntryStatus(str, Enum):
    OBSERVED = "observed"
    WARMING = "warming"
    READY = "ready"
    STALE = "stale"
    EVICTED = "evicted"


# ── Scope context ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopeContext:
    """Immutable scope metadata for cache lookup enforcement.

    All fields are optional; missing fields degrade the scope match.
    """

    thread_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None

    def fingerprint(self) -> str:
        """Deterministic fingerprint for equality comparisons."""
        return sha256_hex(
            f"thread={self.thread_id}|user={self.user_id}|project={self.project_id}"
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
        }


DEFAULT_SCOPE_CONTEXT = ScopeContext()


# ── Index entry ──────────────────────────────────────────────────────────────


@dataclass
class ThreadWakeIndexEntry:
    """A single cache metadata entry.  No raw prompt content is stored.

    The ``scope_id`` field holds a fingerprint derived from the scope
    context (e.g. a thread id hash) so that cross-scope lookups can be
    rejected without storing user identifiers in plaintext.
    """

    cache_key: str
    model_id: str
    backend: str
    prompt_prefix_hash: str
    token_count: int
    status: EntryStatus = EntryStatus.OBSERVED
    scope: str = "thread"
    scope_id: str | None = None
    kv_handle_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hit_count: int = 0
    estimated_memory_bytes: int = 0

    def touch(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)
        self.hit_count += 1

    def public_snapshot(self) -> dict[str, Any]:
        """Return a dict safe for external consumers (no scope_id plaintext)."""
        return {
            "cache_key": self.cache_key,
            "model_id": self.model_id,
            "backend": self.backend,
            "prompt_prefix_hash": self.prompt_prefix_hash,
            "token_count": self.token_count,
            "status": self.status.value,
            "scope": self.scope,
            "kv_handle_id": self.kv_handle_id,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
            "hit_count": self.hit_count,
            "estimated_memory_bytes": self.estimated_memory_bytes,
        }


# ── Stats ────────────────────────────────────────────────────────────────────


@dataclass
class ThreadWakeStats:
    """Safe stats snapshot.  No raw prompt content."""

    entry_count: int = 0
    max_entries: int = 0
    estimated_memory_bytes: int = 0
    max_memory_bytes: int = 0
    hit_count: int = 0
    miss_count: int = 0
    evictions: int = 0
    global_allowed: bool = False
    entries_by_status: dict[str, int] = field(default_factory=dict)
    entries_by_scope: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_count": self.entry_count,
            "max_entries": self.max_entries,
            "estimated_memory_bytes": self.estimated_memory_bytes,
            "max_memory_bytes": self.max_memory_bytes,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "evictions": self.evictions,
            "global_allowed": self.global_allowed,
            "entries_by_status": dict(self.entries_by_status),
            "entries_by_scope": dict(self.entries_by_scope),
        }


# ── Index ────────────────────────────────────────────────────────────────────


class ThreadWakeIndex:
    """In-memory LRU cache metadata index.

    Thread-safe.  Entries are tracked by ``cache_key``; lookups are
    scope-enforced.  Eviction uses entry-count and estimated-memory
    thresholds with least-recently-used policy.

    No KV state is stored — this is a metadata index only.
    """

    def __init__(
        self,
        *,
        max_entries: int = 16,
        max_memory_bytes: int = 1_073_741_824,  # 1 GiB
        bytes_per_token: int = 0,
        allow_global: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, ThreadWakeIndexEntry] = {}
        self._lru: list[str] = []  # cache_key ordered least → most recently used

        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_bytes
        self.bytes_per_token = bytes_per_token
        self.allow_global = allow_global

        # Counters
        self._hit_count: int = 0
        self._miss_count: int = 0
        self._evictions: int = 0

    # ── Public API ─────────────────────────────────────────────────────────

    def put_observation(
        self,
        *,
        cache_key: str,
        model_id: str,
        backend: str,
        prompt_prefix_hash: str,
        token_count: int,
        scope: str = "thread",
        scope_context: ScopeContext = DEFAULT_SCOPE_CONTEXT,
        kv_handle_id: str | None = None,
    ) -> ThreadWakeIndexEntry:
        """Create or update an observed entry.

        If an entry with the same ``cache_key`` already exists, it is
        updated (touch + bump hit_count).  Otherwise a new ``observed``
        entry is created.
        """
        if scope == "global" and not self.allow_global:
            raise ValueError("Global scope is not allowed")

        estimated_memory = self._estimate_memory(token_count)
        scope_id = self._scope_id_for(scope, scope_context)

        with self._lock:
            existing = self._entries.get(cache_key)
            if existing is not None:
                existing.touch()
                existing.token_count = token_count
                existing.estimated_memory_bytes = estimated_memory
                existing.kv_handle_id = kv_handle_id
                existing.scope = scope
                existing.scope_id = scope_id
                self._promote_lru(cache_key)
                return existing

            entry = ThreadWakeIndexEntry(
                cache_key=cache_key,
                model_id=model_id,
                backend=backend,
                prompt_prefix_hash=prompt_prefix_hash,
                token_count=token_count,
                status=EntryStatus.OBSERVED,
                scope=scope,
                scope_id=scope_id,
                kv_handle_id=kv_handle_id,
                estimated_memory_bytes=estimated_memory,
            )
            self._entries[cache_key] = entry
            self._lru.append(cache_key)
            self._evict_if_needed()
            return entry

    def get(
        self,
        cache_key: str,
        scope_context: ScopeContext,
    ) -> ThreadWakeIndexEntry | None:
        """Look up an entry by cache key with scope enforcement.

        Returns ``None`` for a miss (including scope mismatch).
        """
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is None or entry.status == EntryStatus.EVICTED:
                self._miss_count += 1
                return None

            if not self._scope_matches(entry, scope_context):
                self._miss_count += 1
                return None

            if entry.status in (EntryStatus.STALE,):
                # Stale entries can be found but should not be treated as hits
                # for active reuse.  Callers check status.
                self._miss_count += 1
                return None

            entry.touch()
            self._promote_lru(cache_key)
            self._hit_count += 1
            return entry

    def mark_ready(self, cache_key: str) -> None:
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry:
                entry.status = EntryStatus.READY
                self._promote_lru(cache_key)

    def mark_stale(self, cache_key: str) -> None:
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry:
                entry.status = EntryStatus.STALE

    def evict(self, cache_key: str) -> None:
        """Evict a single entry by cache key."""
        with self._lock:
            entry = self._entries.get(cache_key)
            if entry:
                entry.status = EntryStatus.EVICTED
                del self._entries[cache_key]
                if cache_key in self._lru:
                    self._lru.remove(cache_key)
                self._evictions += 1

    def flush(
        self,
        scope: str | None = None,
        *,
        model_id: str | None = None,
        scope_id_hashed: str | None = None,
    ) -> int:
        """Remove entries matching the given filters.

        All filters are AND-ed: only entries matching all non-None
        filters are removed.  Returns the number of entries removed.
        """
        with self._lock:
            if scope is None and model_id is None and scope_id_hashed is None:
                count = len(self._entries)
                for key in list(self._entries):
                    self._entries[key].status = EntryStatus.EVICTED
                self._entries.clear()
                self._lru.clear()
                self._evictions += count
                return count

            to_remove = []
            for key, entry in self._entries.items():
                if scope is not None and entry.scope != scope:
                    continue
                if model_id is not None and entry.model_id != model_id:
                    continue
                if scope_id_hashed is not None and entry.scope_id != scope_id_hashed:
                    continue
                to_remove.append(key)

            for key in to_remove:
                self._entries[key].status = EntryStatus.EVICTED
                del self._entries[key]
                if key in self._lru:
                    self._lru.remove(key)
            self._evictions += len(to_remove)
            return len(to_remove)

    def stats(self) -> ThreadWakeStats:
        """Return a snapshot of current index state."""
        with self._lock:
            total_memory = sum(
                e.estimated_memory_bytes
                for e in self._entries.values()
                if e.status != EntryStatus.EVICTED
            )
            by_status: dict[str, int] = {}
            by_scope: dict[str, int] = {}
            for entry in self._entries.values():
                if entry.status == EntryStatus.EVICTED:
                    continue
                status_key = entry.status.value
                by_status[status_key] = by_status.get(status_key, 0) + 1
                scope_key = entry.scope
                by_scope[scope_key] = by_scope.get(scope_key, 0) + 1

            return ThreadWakeStats(
                entry_count=len(self._entries),
                max_entries=self.max_entries,
                estimated_memory_bytes=total_memory,
                max_memory_bytes=self.max_memory_bytes,
                hit_count=self._hit_count,
                miss_count=self._miss_count,
                evictions=self._evictions,
                global_allowed=self.allow_global,
                entries_by_status=by_status,
                entries_by_scope=by_scope,
            )

    def list_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return public-safe entry snapshots.

        ``scope_id`` is excluded from the output.
        """
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if e.status != EntryStatus.EVICTED
            ]
            entries.sort(key=lambda e: e.last_used_at, reverse=True)
            return [e.public_snapshot() for e in entries[:limit]]

    # ── Internal ────────────────────────────────────────────────────────────

    def _promote_lru(self, cache_key: str) -> None:
        """Move cache_key to the end of the LRU list (most recently used)."""
        if cache_key in self._lru:
            self._lru.remove(cache_key)
        self._lru.append(cache_key)

    def _evict_if_needed(self) -> None:
        """Evict entries until within entry count + memory thresholds."""
        while True:
            total_memory = sum(
                e.estimated_memory_bytes for e in self._entries.values()
            )

            over_entries = len(self._entries) > self.max_entries
            over_memory = (
                self.max_memory_bytes > 0
                and total_memory > self.max_memory_bytes
            )

            if not over_entries and not over_memory:
                break

            if not self._lru:
                break

            lru_key = self._lru[0]
            self._entries[lru_key].status = EntryStatus.EVICTED
            del self._entries[lru_key]
            self._lru.pop(0)
            self._evictions += 1

    def _scope_matches(
        self,
        entry: ThreadWakeIndexEntry,
        ctx: ScopeContext,
    ) -> bool:
        """Return True if the scope context is compatible with the entry."""
        scope = entry.scope

        if scope == "request":
            return True

        if scope == "global":
            return self.allow_global

        if scope == "thread":
            lookup = sha256_hex(ctx.thread_id) if ctx.thread_id else None
            return self._ids_match(lookup, entry.scope_id)

        if scope == "project":
            lookup = sha256_hex(ctx.project_id) if ctx.project_id else None
            return self._ids_match(lookup, entry.scope_id)

        if scope == "user":
            lookup = sha256_hex(ctx.user_id) if ctx.user_id else None
            return self._ids_match(lookup, entry.scope_id)

        return False

    @staticmethod
    def _scope_id_for(scope: str, ctx: ScopeContext) -> str | None:
        """Derive a hashed scope identifier from the context.

        Uses only the relevant field per scope type, hashed for privacy.
        """
        if scope == "request":
            return None
        if scope == "thread":
            raw = ctx.thread_id
        elif scope == "user":
            raw = ctx.user_id
        elif scope == "project":
            raw = ctx.project_id
        elif scope == "global":
            raw = "global"
        else:
            return None
        if raw is None:
            return None
        return sha256_hex(raw)

    @staticmethod
    def _ids_match(lookup_id: str | None, entry_scope_id: str | None) -> bool:
        """Compare a hashed lookup id against an entry's hashed scope_id.

        Both sides are SHA-256 hashes of the relevant scope field.
        If both are None, the match succeeds (neither side supplies scope
        identification).  If only one is None, the match fails.
        """
        if lookup_id is None and entry_scope_id is None:
            return True
        if lookup_id is None or entry_scope_id is None:
            return False
        return lookup_id == entry_scope_id

    def _estimate_memory(self, token_count: int) -> int:
        if self.bytes_per_token <= 0:
            return 0
        return token_count * self.bytes_per_token

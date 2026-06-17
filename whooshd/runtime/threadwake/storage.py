"""Optional SQLite persistence for ThreadWake candidate telemetry.

Provides a ``ThreadWakeStorageProtocol`` with two implementations:
- ``NoOpThreadWakeStorage`` — in-memory only, stores nothing
- ``SQLiteThreadWakeStorage`` — persists candidate metadata to SQLite

SQLite is disabled by default and never required for Whoosh'd startup
or inference.  No raw prompts, token IDs, opaque refs, KV tensors, or
user/thread identifiers are persisted.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

# ── Protocol ───────────────────────────────────────────────────────────────


class ThreadWakeStorageProtocol(Protocol):
    """Protocol for ThreadWake candidate telemetry storage."""

    def upsert_candidate(self, entry: Any) -> None:
        """Insert or update a candidate entry."""
        ...

    def list_candidates(
        self, limit: int = 50,
        min_confidence: str | None = None,
        backend: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def candidate_stats(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


# ── No-op storage ──────────────────────────────────────────────────────────


class NoOpThreadWakeStorage:
    """In-memory-only storage.  Accepts all calls, stores nothing."""

    def upsert_candidate(self, entry: Any) -> None:
        pass

    def list_candidates(
        self, limit: int = 50,
        min_confidence: str | None = None,
        backend: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def candidate_stats(self) -> dict[str, Any]:
        return {
            "total_candidates": 0, "high_confidence": 0,
            "medium_confidence": 0, "low_confidence": 0,
            "candidate_seen_total": 0, "potential_saved_tokens_total": 0,
            "average_candidate_score": 0.0,
        }

    def close(self) -> None:
        pass


# ── SQLite storage ─────────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS threadwake_candidates (
    prefix_hash TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model_id TEXT NOT NULL,
    tokenizer_hash TEXT,
    chat_template_hash TEXT,
    candidate_score REAL,
    candidate_confidence TEXT,
    potential_saved_tokens INTEGER,
    potential_saved_ratio REAL,
    candidate_seen_count INTEGER DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    selection_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidates_backend
    ON threadwake_candidates(backend);
CREATE INDEX IF NOT EXISTS idx_candidates_model_id
    ON threadwake_candidates(model_id);
CREATE INDEX IF NOT EXISTS idx_candidates_confidence
    ON threadwake_candidates(candidate_confidence);
CREATE INDEX IF NOT EXISTS idx_candidates_last_seen_at
    ON threadwake_candidates(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_candidates_score
    ON threadwake_candidates(candidate_score DESC);

CREATE TABLE IF NOT EXISTS threadwake_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SQLiteThreadWakeStorage:
    """SQLite-backed candidate telemetry storage.

    Uses ``sqlite3`` stdlib only.  All writes are best-effort;
    persistence failures are logged and never break inference.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._persistence_errors = 0
        self._upserts_total = 0
        self._init_db()

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()
        except Exception as exc:
            logger.warning("ThreadWake SQLite init failed: %s", exc)
            self._persistence_errors += 1
            if self._conn:
                self._conn.close()
            self._conn = None

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._conn is not None

    @property
    def persistence_errors_total(self) -> int:
        return self._persistence_errors

    @property
    def upserts_total(self) -> int:
        return self._upserts_total

    # ── Public ──────────────────────────────────────────────────────────

    def upsert_candidate(self, entry: Any) -> None:
        if not self._conn:
            return
        try:
            with self._lock:
                prefix_hash = getattr(entry, "prompt_prefix_hash", "")
                if not prefix_hash:
                    return
                self._conn.execute(
                    """INSERT INTO threadwake_candidates
                       (prefix_hash, backend, model_id, tokenizer_hash,
                        chat_template_hash, candidate_score, candidate_confidence,
                        potential_saved_tokens, potential_saved_ratio,
                        candidate_seen_count, first_seen_at, last_seen_at,
                        selection_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'), ?)
                       ON CONFLICT(prefix_hash) DO UPDATE SET
                        candidate_score = excluded.candidate_score,
                        candidate_confidence = excluded.candidate_confidence,
                        potential_saved_tokens = excluded.potential_saved_tokens,
                        potential_saved_ratio = excluded.potential_saved_ratio,
                        candidate_seen_count = threadwake_candidates.candidate_seen_count + 1,
                        last_seen_at = datetime('now'),
                        selection_reason = excluded.selection_reason""",
                    (
                        prefix_hash,
                        getattr(entry, "backend", "") or "",
                        getattr(entry, "model_id", "") or "",
                        getattr(entry, "tokenizer_hash", None),
                        getattr(entry, "chat_template_hash", None),
                        getattr(entry, "candidate_score", None),
                        getattr(entry, "candidate_confidence", None),
                        getattr(entry, "potential_saved_tokens", None),
                        getattr(entry, "potential_saved_ratio", None),
                        getattr(entry, "selection_reason", None),
                    ),
                )
                self._conn.commit()
                self._upserts_total += 1
        except Exception as exc:
            logger.warning("ThreadWake SQLite upsert failed: %s", exc)
            self._persistence_errors += 1

    def list_candidates(
        self,
        limit: int = 50,
        min_confidence: str | None = None,
        backend: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._conn:
            return []
        limit = max(1, min(limit, 500))
        try:
            with self._lock:
                query = "SELECT * FROM threadwake_candidates WHERE 1=1"
                params: list = []
                if min_confidence:
                    query += " AND candidate_confidence = ?"
                    params.append(min_confidence)
                if backend:
                    query += " AND backend = ?"
                    params.append(backend)
                query += " ORDER BY candidate_score DESC, candidate_seen_count DESC, last_seen_at DESC LIMIT ?"
                params.append(limit)
                rows = self._conn.execute(query, params).fetchall()
            return [
                {
                    "prefix_hash": r[0], "backend": r[1], "model_id": r[2],
                    "tokenizer_hash": r[3], "chat_template_hash": r[4],
                    "candidate_score": r[5], "candidate_confidence": r[6],
                    "potential_saved_tokens": r[7], "potential_saved_ratio": r[8],
                    "candidate_seen_count": r[9], "first_seen_at": r[10],
                    "last_seen_at": r[11], "selection_reason": r[12],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("ThreadWake SQLite list failed: %s", exc)
            self._persistence_errors += 1
            return []

    def candidate_stats(self) -> dict[str, Any]:
        if not self._conn:
            return NoOpThreadWakeStorage().candidate_stats()
        try:
            with self._lock:
                row = self._conn.execute(
                    """SELECT COUNT(*),
                              SUM(CASE WHEN candidate_confidence='high' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN candidate_confidence='medium' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN candidate_confidence='low' THEN 1 ELSE 0 END),
                              COALESCE(SUM(candidate_seen_count), 0),
                              COALESCE(SUM(potential_saved_tokens), 0),
                              COALESCE(AVG(candidate_score), 0.0)
                       FROM threadwake_candidates"""
                ).fetchone()
            if row is None:
                return NoOpThreadWakeStorage().candidate_stats()
            return {
                "total_candidates": row[0], "high_confidence": row[1],
                "medium_confidence": row[2], "low_confidence": row[3],
                "candidate_seen_total": row[4], "potential_saved_tokens_total": row[5],
                "average_candidate_score": round(row[6], 4),
            }
        except Exception as exc:
            logger.warning("ThreadWake SQLite stats failed: %s", exc)
            self._persistence_errors += 1
            return NoOpThreadWakeStorage().candidate_stats()

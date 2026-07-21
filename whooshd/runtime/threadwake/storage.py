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
from whooshd.log_safety import exception_metadata

logger = logging.getLogger(__name__)

# ── Protocol ───────────────────────────────────────────────────────────────


class ThreadWakeStorageProtocol(Protocol):
    """Protocol for ThreadWake candidate telemetry storage."""

    def upsert_candidate(self, entry: Any) -> None: ...
    def list_candidates(self, limit=50, min_confidence=None, backend=None) -> list[dict]: ...
    def candidate_stats(self) -> dict: ...
    def upsert_snapshot_manifest(self, manifest: Any) -> None: ...
    def list_snapshot_manifests(self, limit=50, status=None, backend=None) -> list[dict]: ...
    def snapshot_manifest_stats(self) -> dict: ...
    def upsert_snapshot_artifact(self, artifact: Any) -> None: ...
    def list_snapshot_artifacts(self, limit=50, status=None, backend=None) -> list[dict]: ...
    def snapshot_artifact_stats(self) -> dict: ...
    def record_snapshot_material_validation(self, result: Any) -> None: ...
    def upsert_snapshot_material(self, material: Any) -> None: ...
    def list_snapshot_materials(self, limit=50, status=None, backend=None) -> list[dict]: ...
    def snapshot_material_stats(self) -> dict: ...
    def close(self) -> None: ...


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

    def upsert_snapshot_manifest(self, manifest: Any) -> None:
        pass

    def list_snapshot_manifests(self, limit: int = 50, status: str | None = None, backend: str | None = None) -> list[dict[str, Any]]:
        return []

    def snapshot_manifest_stats(self) -> dict[str, Any]:
        return {"total_manifests": 0, "planned": 0, "superseded": 0, "expired": 0, "rejected": 0}

    def upsert_snapshot_artifact(self, artifact: Any) -> None:
        pass

    def list_snapshot_artifacts(self, limit: int = 50, status: str | None = None, backend: str | None = None) -> list[dict[str, Any]]:
        return []

    def snapshot_artifact_stats(self) -> dict[str, Any]:
        return {"total_artifacts": 0, "planned": 0, "build_pending": 0, "build_failed": 0, "ready": 0, "superseded": 0, "expired": 0}

    def record_snapshot_material_validation(self, result: Any) -> None:
        pass

    def upsert_snapshot_material(self, material: Any) -> None:
        pass

    def list_snapshot_materials(self, limit=50, status=None, backend=None) -> list[dict]:
        return []

    def snapshot_material_stats(self) -> dict:
        return {"total_materials": 0, "declared": 0, "materialized": 0, "validated": 0, "invalid": 0, "unsupported": 0, "expired": 0}


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

CREATE TABLE IF NOT EXISTS threadwake_snapshot_manifests (
    manifest_id TEXT PRIMARY KEY,
    prefix_hash TEXT NOT NULL,
    backend TEXT,
    model_id TEXT,
    tokenizer_hash TEXT,
    chat_template_hash TEXT,
    candidate_score REAL,
    candidate_confidence TEXT,
    seen_count INTEGER DEFAULT 0,
    potential_saved_tokens_total INTEGER DEFAULT 0,
    average_potential_saved_ratio REAL,
    eligibility_reason TEXT,
    policy_version TEXT,
    status TEXT DEFAULT 'planned',
    created_at TEXT,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_manifest_prefix_hash
    ON threadwake_snapshot_manifests(prefix_hash);
CREATE INDEX IF NOT EXISTS idx_snapshot_manifest_backend
    ON threadwake_snapshot_manifests(backend);
CREATE INDEX IF NOT EXISTS idx_snapshot_manifest_model_id
    ON threadwake_snapshot_manifests(model_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_manifest_status
    ON threadwake_snapshot_manifests(status);
CREATE INDEX IF NOT EXISTS idx_snapshot_manifest_created_at
    ON threadwake_snapshot_manifests(created_at);

CREATE TABLE IF NOT EXISTS threadwake_snapshot_artifacts (
    artifact_id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    prefix_hash TEXT NOT NULL,
    backend TEXT,
    model_id TEXT,
    tokenizer_hash TEXT,
    chat_template_hash TEXT,
    status TEXT DEFAULT 'planned',
    policy_version TEXT,
    artifact_version TEXT,
    build_attempts INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT,
    updated_at TEXT,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_artifacts_manifest
    ON threadwake_snapshot_artifacts(manifest_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_artifacts_status
    ON threadwake_snapshot_artifacts(status);
CREATE INDEX IF NOT EXISTS idx_snapshot_artifacts_backend
    ON threadwake_snapshot_artifacts(backend);
CREATE INDEX IF NOT EXISTS idx_snapshot_artifacts_created
    ON threadwake_snapshot_artifacts(created_at);

CREATE TABLE IF NOT EXISTS threadwake_snapshot_creation_events (
    event_id TEXT PRIMARY KEY,
    artifact_id TEXT,
    manifest_id TEXT,
    backend TEXT,
    model_id TEXT,
    status TEXT,
    reason TEXT,
    snapshot_ref_hash TEXT,
    error TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_creation_artifact
    ON threadwake_snapshot_creation_events(artifact_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_creation_status
    ON threadwake_snapshot_creation_events(status);
CREATE INDEX IF NOT EXISTS idx_snapshot_creation_reason
    ON threadwake_snapshot_creation_events(reason);
CREATE INDEX IF NOT EXISTS idx_snapshot_creation_created_at
    ON threadwake_snapshot_creation_events(created_at);

CREATE TABLE IF NOT EXISTS threadwake_snapshot_materials (
    material_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    prefix_hash TEXT NOT NULL,
    backend TEXT,
    model_id TEXT,
    tokenizer_hash TEXT,
    chat_template_hash TEXT,
    material_kind TEXT,
    material_status TEXT,
    material_version TEXT,
    policy_version TEXT,
    checksum TEXT,
    byte_size INTEGER,
    token_count INTEGER,
    format_name TEXT,
    format_version TEXT,
    validation_errors TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_material_artifact
    ON threadwake_snapshot_materials(artifact_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_material_manifest
    ON threadwake_snapshot_materials(manifest_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_material_status
    ON threadwake_snapshot_materials(material_status);
CREATE INDEX IF NOT EXISTS idx_snapshot_material_backend
    ON threadwake_snapshot_materials(backend);
CREATE INDEX IF NOT EXISTS idx_snapshot_material_created
    ON threadwake_snapshot_materials(created_at);

CREATE TABLE IF NOT EXISTS threadwake_snapshot_material_validation_events (
    event_id TEXT PRIMARY KEY,
    material_id TEXT,
    valid INTEGER,
    reason TEXT,
    material_kind TEXT,
    material_status TEXT,
    errors TEXT,
    checked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_material_validation_material
    ON threadwake_snapshot_material_validation_events(material_id);
CREATE INDEX IF NOT EXISTS idx_material_validation_valid
    ON threadwake_snapshot_material_validation_events(valid);
CREATE INDEX IF NOT EXISTS idx_material_validation_reason
    ON threadwake_snapshot_material_validation_events(reason);
CREATE INDEX IF NOT EXISTS idx_material_validation_checked_at
    ON threadwake_snapshot_material_validation_events(checked_at);
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
            logger.warning("ThreadWake SQLite init failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            if self._conn:
                self._conn.close()
            self._conn = None

    def record_snapshot_material_validation(self, result: Any) -> None:
        if not self._conn: return
        try:
            import uuid
            with self._lock:
                self._conn.execute(
                    "INSERT INTO threadwake_snapshot_material_validation_events (event_id,material_id,valid,reason,material_kind,material_status,errors,checked_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], getattr(result,"material_id",None),
                     1 if getattr(result,"valid",False) else 0,
                     getattr(result,"reason",""), getattr(result,"material_kind",None),
                     getattr(result,"material_status",None),
                     ",".join(getattr(result,"errors",[]) or []),
                     getattr(result,"checked_at","")),
                )
                self._conn.commit()
        except Exception as exc:
            logger.warning("ThreadWake SQLite validation record failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1

    # ── Snapshot materials ─────────────────────────────────────────────

    def upsert_snapshot_material(self, material: Any) -> None:
        if not self._conn: return
        try:
            with self._lock:
                mid = getattr(material, "material_id", "")
                if not mid: return
                self._conn.execute(
                    """INSERT INTO threadwake_snapshot_materials
                       (material_id,artifact_id,manifest_id,prefix_hash,backend,model_id,
                        tokenizer_hash,chat_template_hash,material_kind,material_status,
                        material_version,policy_version,checksum,byte_size,token_count,
                        format_name,format_version,validation_errors,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(material_id) DO UPDATE SET
                        material_status=excluded.material_status,
                        material_kind=excluded.material_kind,
                        checksum=excluded.checksum, byte_size=excluded.byte_size,
                        token_count=excluded.token_count,
                        validation_errors=excluded.validation_errors,
                        updated_at=excluded.updated_at""",
                    (mid, getattr(material,"artifact_id",""),getattr(material,"manifest_id",""),
                     getattr(material,"prefix_hash",""),getattr(material,"backend",None),
                     getattr(material,"model_id",None),getattr(material,"tokenizer_hash",None),
                     getattr(material,"chat_template_hash",None),getattr(material,"material_kind","metadata_only"),
                     getattr(material,"material_status","declared"),getattr(material,"material_version","1"),
                     getattr(material,"policy_version","1"),getattr(material,"checksum",None),
                     getattr(material,"byte_size",None),getattr(material,"token_count",None),
                     getattr(material,"format_name",None),getattr(material,"format_version",None),
                     ",".join(getattr(material,"validation_errors",[]) or []),
                     getattr(material,"created_at",""),getattr(material,"updated_at","")),
                )
                self._conn.commit()
        except Exception as exc:
            logger.warning("ThreadWake SQLite material upsert failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1

    def list_snapshot_materials(self, limit=50, status=None, backend=None):
        if not self._conn: return []
        limit = max(1, min(limit, 500))
        try:
            with self._lock:
                q = "SELECT * FROM threadwake_snapshot_materials WHERE 1=1"
                ps: list = []
                if status: q += " AND material_status=?"; ps.append(status)
                if backend: q += " AND backend=?"; ps.append(backend)
                q += " ORDER BY created_at DESC LIMIT ?"; ps.append(limit)
                rows = self._conn.execute(q, ps).fetchall()
            return [{"material_id":r[0],"artifact_id":r[1],"manifest_id":r[2],"prefix_hash":r[3],
                     "backend":r[4],"model_id":r[5],"tokenizer_hash":r[6],"chat_template_hash":r[7],
                     "material_kind":r[8],"material_status":r[9],"material_version":r[10],
                     "policy_version":r[11],"checksum":r[12],"byte_size":r[13],"token_count":r[14],
                     "format_name":r[15],"format_version":r[16],"validation_errors":r[17],
                     "created_at":r[18],"updated_at":r[19]} for r in rows]
        except Exception as exc:
            logger.warning("ThreadWake SQLite material list failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return []

    def snapshot_material_stats(self):
        if not self._conn: return NoOpThreadWakeStorage().snapshot_material_stats()
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN material_status='declared' THEN 1 ELSE 0 END), SUM(CASE WHEN material_status='materialized' THEN 1 ELSE 0 END), SUM(CASE WHEN material_status='validated' THEN 1 ELSE 0 END), SUM(CASE WHEN material_status='invalid' THEN 1 ELSE 0 END), SUM(CASE WHEN material_status='unsupported' THEN 1 ELSE 0 END), SUM(CASE WHEN material_status='expired' THEN 1 ELSE 0 END) FROM threadwake_snapshot_materials"
                ).fetchone()
            if row is None: return NoOpThreadWakeStorage().snapshot_material_stats()
            return {"total_materials":row[0],"declared":row[1],"materialized":row[2],"validated":row[3],"invalid":row[4],"unsupported":row[5],"expired":row[6]}
        except Exception as exc:
            logger.warning("ThreadWake SQLite material stats failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return NoOpThreadWakeStorage().snapshot_material_stats()

    # ── Snapshot artifacts ─────────────────────────────────────────────

    def upsert_snapshot_artifact(self, artifact: Any) -> None:
        if not self._conn: return
        try:
            with self._lock:
                aid = getattr(artifact, "artifact_id", "")
                if not aid: return
                self._conn.execute(
                    """INSERT INTO threadwake_snapshot_artifacts
                       (artifact_id, manifest_id, prefix_hash, backend, model_id,
                        tokenizer_hash, chat_template_hash, status, policy_version,
                        artifact_version, build_attempts, notes, created_at, updated_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                        status=excluded.status, build_attempts=build_attempts+1,
                        notes=COALESCE(excluded.notes, notes),
                        updated_at=excluded.updated_at,
                        last_seen_at=excluded.last_seen_at""",
                    (aid, getattr(artifact, "manifest_id", ""), getattr(artifact, "prefix_hash", ""),
                     getattr(artifact, "backend", None), getattr(artifact, "model_id", None),
                     getattr(artifact, "tokenizer_hash", None), getattr(artifact, "chat_template_hash", None),
                     getattr(artifact, "status", "planned"), getattr(artifact, "policy_version", "1"),
                     getattr(artifact, "artifact_version", "1"), getattr(artifact, "build_attempts", 0),
                     getattr(artifact, "notes", None), getattr(artifact, "created_at", ""),
                     getattr(artifact, "updated_at", ""), getattr(artifact, "last_seen_at", None)),
                )
                self._conn.commit()
        except Exception as exc:
            logger.warning("ThreadWake SQLite artifact upsert failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1

    def list_snapshot_artifacts(self, limit=50, status=None, backend=None):
        if not self._conn: return []
        limit = max(1, min(limit, 500))
        try:
            with self._lock:
                q = "SELECT * FROM threadwake_snapshot_artifacts WHERE 1=1"
                ps: list = []
                if status: q += " AND status=?"; ps.append(status)
                if backend: q += " AND backend=?"; ps.append(backend)
                q += " ORDER BY created_at DESC LIMIT ?"; ps.append(limit)
                rows = self._conn.execute(q, ps).fetchall()
            return [{"artifact_id":r[0],"manifest_id":r[1],"prefix_hash":r[2],"backend":r[3],
                     "model_id":r[4],"tokenizer_hash":r[5],"chat_template_hash":r[6],
                     "status":r[7],"policy_version":r[8],"artifact_version":r[9],
                     "build_attempts":r[10],"notes":r[11],"created_at":r[12],
                     "updated_at":r[13],"last_seen_at":r[14]} for r in rows]
        except Exception as exc:
            logger.warning("ThreadWake SQLite artifact list failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return []

    def snapshot_artifact_stats(self):
        if not self._conn: return NoOpThreadWakeStorage().snapshot_artifact_stats()
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN status='planned' THEN 1 ELSE 0 END), SUM(CASE WHEN status='build_pending' THEN 1 ELSE 0 END), SUM(CASE WHEN status='build_failed' THEN 1 ELSE 0 END), SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END), SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END), SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) FROM threadwake_snapshot_artifacts"
                ).fetchone()
            if row is None: return NoOpThreadWakeStorage().snapshot_artifact_stats()
            return {"total_artifacts":row[0],"planned":row[1],"build_pending":row[2],"build_failed":row[3],"ready":row[4],"superseded":row[5],"expired":row[6]}
        except Exception as exc:
            logger.warning("ThreadWake SQLite artifact stats failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return NoOpThreadWakeStorage().snapshot_artifact_stats()

    # ── Snapshot manifests ─────────────────────────────────────────────

    def upsert_snapshot_manifest(self, manifest: Any) -> None:
        if not self._conn:
            return
        try:
            with self._lock:
                mid = getattr(manifest, "manifest_id", "")
                if not mid:
                    return
                self._conn.execute(
                    """INSERT INTO threadwake_snapshot_manifests
                       (manifest_id, prefix_hash, backend, model_id,
                        tokenizer_hash, chat_template_hash, candidate_score,
                        candidate_confidence, seen_count, potential_saved_tokens_total,
                        average_potential_saved_ratio, eligibility_reason,
                        policy_version, status, created_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(manifest_id) DO UPDATE SET
                        candidate_score = excluded.candidate_score,
                        candidate_confidence = excluded.candidate_confidence,
                        seen_count = excluded.seen_count,
                        potential_saved_tokens_total = excluded.potential_saved_tokens_total,
                        average_potential_saved_ratio = excluded.average_potential_saved_ratio,
                        eligibility_reason = excluded.eligibility_reason,
                        policy_version = excluded.policy_version,
                        status = excluded.status,
                        last_seen_at = excluded.last_seen_at""",
                    (
                        mid, getattr(manifest, "prefix_hash", ""),
                        getattr(manifest, "backend", None),
                        getattr(manifest, "model_id", None),
                        getattr(manifest, "tokenizer_hash", None),
                        getattr(manifest, "chat_template_hash", None),
                        getattr(manifest, "candidate_score", 0),
                        getattr(manifest, "candidate_confidence", None),
                        getattr(manifest, "seen_count", 0),
                        getattr(manifest, "potential_saved_tokens_total", 0),
                        getattr(manifest, "average_potential_saved_ratio", 0),
                        getattr(manifest, "eligibility_reason", ""),
                        getattr(manifest, "policy_version", "1"),
                        getattr(manifest, "status", "planned"),
                        getattr(manifest, "created_at", ""),
                        getattr(manifest, "last_seen_at", None),
                    ),
                )
                self._conn.commit()
        except Exception as exc:
            logger.warning("ThreadWake SQLite manifest upsert failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1

    def list_snapshot_manifests(
        self, limit: int = 50, status: str | None = None, backend: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._conn:
            return []
        limit = max(1, min(limit, 500))
        try:
            with self._lock:
                query = "SELECT * FROM threadwake_snapshot_manifests WHERE 1=1"
                params: list = []
                if status:
                    query += " AND status = ?"
                    params.append(status)
                if backend:
                    query += " AND backend = ?"
                    params.append(backend)
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                rows = self._conn.execute(query, params).fetchall()
            return [
                {
                    "manifest_id": r[0], "prefix_hash": r[1], "backend": r[2],
                    "model_id": r[3], "tokenizer_hash": r[4], "chat_template_hash": r[5],
                    "candidate_score": r[6], "candidate_confidence": r[7],
                    "seen_count": r[8], "potential_saved_tokens_total": r[9],
                    "average_potential_saved_ratio": r[10], "eligibility_reason": r[11],
                    "policy_version": r[12], "status": r[13],
                    "created_at": r[14], "last_seen_at": r[15],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("ThreadWake SQLite manifest list failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return []

    def snapshot_manifest_stats(self) -> dict[str, Any]:
        if not self._conn:
            return NoOpThreadWakeStorage().snapshot_manifest_stats()
        try:
            with self._lock:
                row = self._conn.execute(
                    """SELECT COUNT(*),
                              SUM(CASE WHEN status='planned' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END)
                       FROM threadwake_snapshot_manifests"""
                ).fetchone()
            if row is None:
                return NoOpThreadWakeStorage().snapshot_manifest_stats()
            return {
                "total_manifests": row[0], "planned": row[1],
                "superseded": row[2], "expired": row[3], "rejected": row[4],
            }
        except Exception as exc:
            logger.warning("ThreadWake SQLite manifest stats failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return NoOpThreadWakeStorage().snapshot_manifest_stats()

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
            logger.warning("ThreadWake SQLite upsert failed diagnostic=%s", exception_metadata(exc))
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
            logger.warning("ThreadWake SQLite list failed diagnostic=%s", exception_metadata(exc))
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
            logger.warning("ThreadWake SQLite stats failed diagnostic=%s", exception_metadata(exc))
            self._persistence_errors += 1
            return NoOpThreadWakeStorage().candidate_stats()

"""Tests for ThreadWake SQLite candidate storage."""

from __future__ import annotations

import json
import os
import tempfile

from whooshd.runtime.threadwake.index import ThreadWakeIndexEntry
from whooshd.runtime.threadwake.storage import (
    NoOpThreadWakeStorage,
    SQLiteThreadWakeStorage,
)


def _candidate(**overrides):
    defaults = {
        "prompt_prefix_hash": "abc123", "backend": "fake",
        "model_id": "test-model", "tokenizer_hash": "tok-001",
        "chat_template_hash": "tmpl-001", "candidate_score": 0.8,
        "candidate_confidence": "high", "potential_saved_tokens": 500,
        "potential_saved_ratio": 0.5, "selection_reason": "proof_compatible",
    }
    defaults.update(overrides)
    e = ThreadWakeIndexEntry(
        cache_key="k", model_id=defaults["model_id"],
        backend=defaults["backend"],
        prompt_prefix_hash=defaults["prompt_prefix_hash"],
        token_count=100,
        tokenizer_hash=defaults["tokenizer_hash"],
        chat_template_hash=defaults["chat_template_hash"],
        candidate_score=defaults["candidate_score"],
        candidate_confidence=defaults["candidate_confidence"],
        potential_saved_tokens=defaults["potential_saved_tokens"],
        potential_saved_ratio=defaults["potential_saved_ratio"],
        selection_reason=defaults["selection_reason"],
    )
    return e


# ── No-op storage ─────────────────────────────────────────────────────────


class TestNoOpStorage:
    def test_upsert_does_nothing(self):
        s = NoOpThreadWakeStorage()
        s.upsert_candidate(_candidate())  # Should not raise

    def test_list_returns_empty(self):
        s = NoOpThreadWakeStorage()
        assert s.list_candidates() == []

    def test_stats_returns_zeros(self):
        s = NoOpThreadWakeStorage()
        stats = s.candidate_stats()
        assert stats["total_candidates"] == 0


# ── SQLite storage ────────────────────────────────────────────────────────


class TestSQLiteStorage:
    @staticmethod
    def _make_db():
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        return SQLiteThreadWakeStorage(path), path

    def test_initializes_schema(self):
        s, path = self._make_db()
        assert s.enabled is True
        s.close()
        os.unlink(path)

    def test_upsert_inserts_candidate(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate(prompt_prefix_hash="hash-a"))
        rows = s.list_candidates()
        assert len(rows) == 1
        assert rows[0]["prefix_hash"] == "hash-a"
        s.close()
        os.unlink(path)

    def test_upsert_updates_existing(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate(prompt_prefix_hash="hash-b", candidate_score=0.5, candidate_seen_count=0))
        s.upsert_candidate(_candidate(prompt_prefix_hash="hash-b", candidate_score=0.9, candidate_seen_count=0))
        rows = s.list_candidates()
        assert len(rows) == 1
        assert rows[0]["candidate_score"] == 0.9
        assert rows[0]["candidate_seen_count"] >= 2
        s.close()
        os.unlink(path)

    def test_filters_by_confidence(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate(prompt_prefix_hash="h1", candidate_confidence="high"))
        s.upsert_candidate(_candidate(prompt_prefix_hash="h2", candidate_confidence="low"))
        high = s.list_candidates(min_confidence="high")
        assert len(high) == 1
        assert high[0]["candidate_confidence"] == "high"
        s.close()
        os.unlink(path)

    def test_filters_by_backend(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate(prompt_prefix_hash="h1", backend="mlx"))
        s.upsert_candidate(_candidate(prompt_prefix_hash="h2", backend="fake"))
        mlx_only = s.list_candidates(backend="mlx")
        assert len(mlx_only) == 1
        assert mlx_only[0]["backend"] == "mlx"
        s.close()
        os.unlink(path)

    def test_candidate_stats(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate(prompt_prefix_hash="h1", candidate_score=0.8, candidate_confidence="high"))
        s.upsert_candidate(_candidate(prompt_prefix_hash="h2", candidate_score=0.4, candidate_confidence="low"))
        stats = s.candidate_stats()
        assert stats["total_candidates"] == 2
        assert stats["high_confidence"] == 1
        assert stats["low_confidence"] == 1
        assert stats["average_candidate_score"] == 0.6
        s.close()
        os.unlink(path)

    def test_output_no_raw_token_ids(self):
        s, path = self._make_db()
        s.upsert_candidate(_candidate())
        rows = s.list_candidates()
        for r in rows:
            assert "token_ids" not in json.dumps(r)
            assert "opaque_ref" not in json.dumps(r)
        s.close()
        os.unlink(path)

    def test_persistence_error_does_not_crash(self):
        """Upsert against a closed DB should not raise."""
        s, path = self._make_db()
        s.close()
        s.upsert_candidate(_candidate())  # Should not raise
        os.unlink(path)

    def test_disabled_does_not_create_db(self):
        """When path is invalid, enabled should be False."""
        s, path = self._make_db()
        s.close()
        os.unlink(path)
        # Invalid path — should be disabled
        s2 = SQLiteThreadWakeStorage("/nonexistent/path/should/not/exist/db.sqlite3")
        assert s2.enabled is False
        s2.close()

    def test_schema_version_table_exists(self):
        s, path = self._make_db()
        rows = s._conn.execute("SELECT version FROM threadwake_schema_version").fetchall()
        assert len(rows) == 0  # No version inserted yet — table exists
        s.close()
        os.unlink(path)

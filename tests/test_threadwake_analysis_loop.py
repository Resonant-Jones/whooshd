"""Tests for M20 metadata-only analysis loop."""

from __future__ import annotations

from whooshd.runtime.threadwake.analysis_loop import ThreadWakeAnalysisLoop
from whooshd.runtime.threadwake.artifacts import SnapshotArtifactRegistry
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex


def _build_index_with_candidates() -> ThreadWakeIndex:
    index = ThreadWakeIndex(max_entries=50)
    for i in range(5):
        key = f"key-{i}"
        index.put_observation(
            cache_key=key, model_id="m", backend="mlx",
            prompt_prefix_hash=f"hash-{i}", token_count=100 + i * 50,
            scope="thread", scope_context=ScopeContext(thread_id="t1"),
        )
        conf = ["high", "medium", "low", "high", "medium"][i]
        score = [0.95, 0.85, 0.85, 0.90, 0.85][i]
        saved = [5000, 3000, 1000, 4000, 2000][i]
        # Mark multiple times to meet minimum_seen_count=5
        for _ in range(8):
            index.mark_candidate_selected(key, score=score, confidence=conf,
                selection_reason="proof", potential_saved_tokens=saved,
                potential_saved_ratio=score)
    return index


class TestLoopBasics:
    def test_run_scans_candidates(self):
        index = _build_index_with_candidates()
        reg = SnapshotArtifactRegistry()
        loop = ThreadWakeAnalysisLoop(index=index, artifact_registry=reg)
        result = loop.run(limit=10)
        assert result.candidates_scanned == 5
        assert result.candidates_eligible >= 1
        assert result.errors == 0

    def test_loop_produces_artifacts(self):
        index = _build_index_with_candidates()
        reg = SnapshotArtifactRegistry()
        loop = ThreadWakeAnalysisLoop(index=index, artifact_registry=reg)
        result = loop.run(limit=10)
        assert result.artifacts_registered >= 1
        assert reg.artifact_stats()["total_artifacts"] >= 1

    def test_loop_produces_manifests(self):
        index = _build_index_with_candidates()
        reg = SnapshotArtifactRegistry()
        loop = ThreadWakeAnalysisLoop(index=index, artifact_registry=reg)
        result = loop.run(limit=10)
        assert result.manifests_created >= 1

    def test_no_index_returns_empty(self):
        loop = ThreadWakeAnalysisLoop()
        result = loop.run()
        assert result.candidates_scanned == 0

    def test_empty_index_safe(self):
        index = ThreadWakeIndex()
        loop = ThreadWakeAnalysisLoop(index=index)
        result = loop.run()
        assert result.candidates_scanned == 0
        assert result.errors == 0

    def test_last_result_tracks(self):
        index = _build_index_with_candidates()
        loop = ThreadWakeAnalysisLoop(index=index)
        loop.run(limit=10)
        lr = loop.last_result()
        assert lr is not None
        assert lr["candidates_scanned"] == 5

    def test_run_count_increments(self):
        index = _build_index_with_candidates()
        loop = ThreadWakeAnalysisLoop(index=index)
        loop.run()
        loop.run()
        assert loop.run_count == 2

    def test_safe_dict_no_raw_content(self):
        index = _build_index_with_candidates()
        loop = ThreadWakeAnalysisLoop(index=index)
        result = loop.run()
        d = result.safe_dict()
        assert "token_ids" not in str(d)
        assert "opaque_ref" not in str(d)

    def test_no_storage_is_safe(self):
        index = _build_index_with_candidates()
        loop = ThreadWakeAnalysisLoop(index=index, storage=None)
        result = loop.run()
        assert result.errors == 0

    def test_no_kv_mutation(self):
        """The loop must not create KV handles or mutate backend state."""
        index = _build_index_with_candidates()
        reg = SnapshotArtifactRegistry()
        loop = ThreadWakeAnalysisLoop(index=index, artifact_registry=reg)
        loop.run()
        # Verify index entries still have no KV handle
        for key in [f"key-{i}" for i in range(5)]:
            entry = index.get(key, ScopeContext(thread_id="t1"))
            if entry:
                assert entry.kv_handle_id is None  # Loop never creates KV

    def test_loop_does_not_touch_inference(self):
        """Loop is callable without any model or backend active."""
        index = _build_index_with_candidates()
        loop = ThreadWakeAnalysisLoop(index=index)
        result = loop.run()
        assert result.candidates_scanned >= 1
        # Should complete without importing mlx, llama_cpp, or touching app.py

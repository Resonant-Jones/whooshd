"""Tests for M21 analysis visibility."""

from __future__ import annotations

import json

from whooshd.runtime.threadwake.analysis_loop import ThreadWakeAnalysisLoop
from whooshd.runtime.threadwake.index import ScopeContext, ThreadWakeIndex


def _build_index():
    index = ThreadWakeIndex(max_entries=50)
    for i in range(3):
        key = f"key-{i}"
        index.put_observation(cache_key=key, model_id="m", backend="mlx",
            prompt_prefix_hash=f"hash-{i}", token_count=100,
            scope="thread", scope_context=ScopeContext(thread_id="t1"))
        for _ in range(8):
            index.mark_candidate_selected(key, score=0.90, confidence="high",
                selection_reason="proof", potential_saved_tokens=2000,
                potential_saved_ratio=0.85)
    return index


class TestVisibility:
    def test_empty_index_is_safe(self):
        loop = ThreadWakeAnalysisLoop(index=ThreadWakeIndex())
        result = loop.run()
        last = loop.last_result()
        assert last is not None
        assert last["candidates_scanned"] == 0
        assert last["errors"] == 0

    def test_result_is_counts_only(self):
        loop = ThreadWakeAnalysisLoop(index=_build_index())
        result = loop.run()
        d = result.safe_dict()
        for val in d.values():
            assert isinstance(val, int)

    def test_no_raw_content_in_result(self):
        loop = ThreadWakeAnalysisLoop(index=_build_index())
        result = loop.run()
        d = result.safe_dict()
        j = json.dumps(d)
        assert "token_ids" not in j
        assert "opaque_ref" not in j
        assert "prompt" not in j

    def test_run_count_increments(self):
        loop = ThreadWakeAnalysisLoop(index=_build_index())
        loop.run()
        loop.run()
        assert loop.run_count == 2

    def test_no_backend_calls(self):
        loop = ThreadWakeAnalysisLoop(index=_build_index())
        result = loop.run()
        assert result.candidates_scanned >= 1

    def test_last_result_none_before_run(self):
        loop = ThreadWakeAnalysisLoop(index=_build_index())
        assert loop.last_result() is None

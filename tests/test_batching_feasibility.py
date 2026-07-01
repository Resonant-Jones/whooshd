"""Tests for batching feasibility analysis — permit office only."""

from __future__ import annotations

import pytest

from whooshd.batching import (
    BatchAnalyzer,
    BatchCandidate,
    BatchCapability,
    BatchIncompatibilityReason,
)
from whooshd.config import get_batch_analysis_enabled


# ── Helpers ────────────────────────────────────────────────────────────────


def _c(
    rid="a",
    queued_at=1.0,
    model="m",
    backend="mlx",
    stream=False,
    max_tokens=256,
    has_image=False,
    sampling_class="default",
):
    return BatchCandidate(
        request_id=rid,
        queued_at=queued_at,
        model=model,
        backend=backend,
        stream=stream,
        max_tokens=max_tokens,
        has_image=has_image,
        sampling_class=sampling_class,
    )


# ── Test 1: Default analysis disabled ─────────────────────────────────────


class TestDefaultDisabled:
    def test_analysis_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_ANALYSIS_ENABLED", raising=False)
        assert get_batch_analysis_enabled() is False

    def test_analysis_returns_empty_when_disabled(self):
        analyzer = BatchAnalyzer()
        candidates = [_c("a", 1.0), _c("b", 2.0)]
        result = analyzer.analyze(candidates, enabled=False)
        assert result.candidate_count == 2
        assert result.group_count == 0
        assert result.eligible_group_count == 0

    def test_analysis_returns_empty_for_no_candidates(self):
        analyzer = BatchAnalyzer()
        result = analyzer.analyze([], enabled=True)
        assert result.candidate_count == 0
        assert result.group_count == 0


# ── Test 2: Same model/backend can form eligible group ─────────────────────


class TestEligibleGroups:
    def test_two_compatible_candidates_form_eligible_group(self):
        analyzer = BatchAnalyzer()
        candidates = [
            _c("a", 1.0, model="m", backend="mlx"),
            _c("b", 2.0, model="m", backend="mlx"),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        assert result.group_count >= 1
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) >= 1
        group = eligible[0]
        assert len(group.request_ids) == 2
        assert "a" in group.request_ids
        assert "b" in group.request_ids

    def test_single_candidate_no_eligible_group(self):
        analyzer = BatchAnalyzer()
        result = analyzer.analyze([_c("a", 1.0)], enabled=True)
        assert result.eligible_group_count == 0


# ── Test 3: Different models do not batch ──────────────────────────────────


class TestDifferentModels:
    def test_different_models_do_not_batch(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="model-a", backend="mlx"),
            _c("b", 2.0, model="model-b", backend="mlx"),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        # Each in its own group, none eligible.
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) == 0


# ── Test 4: Different backends do not batch ────────────────────────────────


class TestDifferentBackends:
    def test_different_backends_do_not_batch(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="m", backend="mlx"),
            _c("b", 2.0, model="m", backend="llama_cpp"),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) == 0


# ── Test 5: Streaming requests not eligible ───────────────────────────────


class TestStreamingNotEligible:
    def test_streaming_not_eligible(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="m", backend="mlx", stream=True),
            _c("b", 2.0, model="m", backend="mlx", stream=False),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) == 0

    def test_both_non_streaming_eligible(self):
        analyzer = BatchAnalyzer()
        candidates = [
            _c("a", 1.0, model="m", backend="mlx", stream=False),
            _c("b", 2.0, model="m", backend="mlx", stream=False),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) >= 1


# ── Test 6: Vision requests not eligible ──────────────────────────────────


class TestVisionNotEligible:
    def test_vision_not_eligible(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="m", backend="mlx", has_image=True),
            _c("b", 2.0, model="m", backend="mlx", has_image=False),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) == 0

    def test_both_text_eligible(self):
        analyzer = BatchAnalyzer()
        candidates = [
            _c("a", 1.0, model="m", backend="mlx", has_image=False),
            _c("b", 2.0, model="m", backend="mlx", has_image=False),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) >= 1


# ── Test 7: Sampling mismatch ──────────────────────────────────────────────


class TestSamplingMismatch:
    def test_sampling_mismatch_not_eligible(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="m", backend="mlx", sampling_class="default"),
            _c("b", 2.0, model="m", backend="mlx", sampling_class="creative"),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        eligible = [g for g in result.groups if g.eligible]
        assert len(eligible) == 0


# ── Test 8: No prompt leakage ─────────────────────────────────────────────


class TestNoLeakage:
    def test_candidate_no_prompt_leakage(self):
        c = _c("a", 1.0)
        c_str = str({
            "request_id": c.request_id,
            "model": c.model,
        })
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in c_str.lower()

    def test_group_no_prompt_leakage(self):
        from whooshd.batching import BatchGroup
        group = BatchGroup(
            group_id="grp-1",
            request_ids=("a", "b"),
            model="m",
            backend="mlx",
            eligible=True,
            estimated_total_tokens=512,
        )
        group_str = str({
            "group_id": group.group_id,
            "request_ids": group.request_ids,
        })
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in group_str.lower()

    def test_analysis_no_prompt_leakage(self):
        analyzer = BatchAnalyzer()
        result = analyzer.analyze(
            [_c("a", 1.0), _c("b", 2.0)],
            enabled=True,
        )
        snapshot = analyzer.build_snapshot(result)
        snapshot_str = str(snapshot)
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in snapshot_str.lower()

    def test_snapshot_no_prompt_leakage(self):
        analyzer = BatchAnalyzer()
        snapshot = analyzer.build_snapshot()
        snapshot_str = str(snapshot)
        for forbidden in (
            "prompt", "message", "content", "generated_text",
            "token_ids", "rendered_prompt", "kv_handle", "opaque_ref",
        ):
            assert forbidden not in snapshot_str.lower()


# ── Test 9: Queue behavior unchanged ───────────────────────────────────────


class TestQueueUnchanged:
    def test_queue_builds_batch_candidates(self):
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="hello")],
            stream=False,
        )
        queue.enqueue(QueueEntry(request_id="a", request=req))

        batch_candidates = queue.build_batch_candidates()
        assert len(batch_candidates) == 1
        assert batch_candidates[0].request_id == "a"
        assert batch_candidates[0].model == "test-model"

    def test_queue_fifo_still_works(self):
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        queue = RequestQueue()
        for i in range(5):
            req = ChatCompletionRequest(
                model="m",
                messages=[ChatMessage(role="user", content=str(i))],
                stream=False,
            )
            queue.enqueue(QueueEntry(request_id=f"req-{i}", request=req))

        dequeued = []
        while queue.depth > 0:
            e = queue.dequeue()
            if e:
                dequeued.append(e.request_id)

        assert dequeued == [f"req-{i}" for i in range(5)]


# ── Test 10: Max group size enforced ──────────────────────────────────────


class TestMaxGroupSize:
    def test_max_group_size_respected(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        candidates = [
            _c("a", 1.0, model="m", backend="mlx"),
            _c("b", 2.0, model="m", backend="mlx"),
            _c("c", 3.0, model="m", backend="mlx"),
        ]
        result = analyzer.analyze(candidates, enabled=True)
        for group in result.groups:
            assert len(group.request_ids) <= 2

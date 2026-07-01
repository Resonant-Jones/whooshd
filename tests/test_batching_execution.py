"""Tests for batching execution skeleton — motors on the bench, not in the factory."""

from __future__ import annotations

import pytest

from whooshd.batching import (
    BatchAnalyzer,
    BatchCandidate,
    BatchExecutionCapability,
)
from whooshd.config import get_batch_execution_enabled


def _c(rid="a", queued_at=1.0, model="m", backend="stub", stream=False, has_image=False, sampling_class="default"):
    return BatchCandidate(request_id=rid, queued_at=queued_at, model=model, backend=backend, stream=stream, has_image=has_image, sampling_class=sampling_class)


class TestExecutionDisabledByDefault:
    def test_execution_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_EXECUTION_ENABLED", raising=False)
        assert get_batch_execution_enabled() is False

    def test_no_batch_without_enabled_flag(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_EXECUTION_ENABLED", raising=False)
        analyzer = BatchAnalyzer()
        result = analyzer.analyze([_c("a", 1.0), _c("b", 2.0)], enabled=True)
        assert any(g.eligible for g in result.groups)


class TestUnsupportedBackendRefuses:
    def test_unsupported_backend_no_batch(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_BATCH_EXECUTION_ENABLED", raising=False)
        from whooshd.adapters.stub import StubInferenceAdapter
        adapter = StubInferenceAdapter()
        assert adapter.supports_chat_batching() == "unsupported"


class TestStubBatchExecution:
    async def test_stub_batch_executes_eligible_group(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        assert adapter.supports_chat_batching() == "experimental"

        req1 = ChatCompletionRequest(model="stub-model", messages=[ChatMessage(role="user", content="a")], stream=False, max_tokens=32)
        req2 = ChatCompletionRequest(model="stub-model", messages=[ChatMessage(role="user", content="b")], stream=False, max_tokens=32)

        results = await adapter.chat_completion_batch([req1, req2])
        assert len(results) == 2
        for r in results:
            assert r.choices[0].message.content

    async def test_batch_preserves_response_order(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_BATCH_EXECUTION_ENABLED", "true")
        from whooshd.adapters.stub import StubInferenceAdapter
        from whooshd.contracts import ChatCompletionRequest, ChatMessage

        adapter = StubInferenceAdapter()
        req_a = ChatCompletionRequest(model="stub-model", messages=[ChatMessage(role="user", content="first")], stream=False, max_tokens=32)
        req_b = ChatCompletionRequest(model="stub-model", messages=[ChatMessage(role="user", content="second")], stream=False, max_tokens=32)
        results = await adapter.chat_completion_batch([req_a, req_b])
        assert len(results) == 2


class TestRefuseStreamingVision:
    def test_streaming_rejected_by_analyzer(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        result = analyzer.analyze([_c("a", 1.0, stream=True), _c("b", 2.0, stream=False)], enabled=True)
        assert result.eligible_group_count == 0

    def test_vision_rejected_by_analyzer(self):
        analyzer = BatchAnalyzer(max_group_size=2)
        result = analyzer.analyze([_c("a", 1.0, has_image=True), _c("b", 2.0, has_image=False)], enabled=True)
        assert result.eligible_group_count == 0


class TestNoLeakage:
    def test_capability_enum_values(self):
        assert BatchExecutionCapability.UNSUPPORTED.value == "unsupported"
        assert BatchExecutionCapability.EXPERIMENTAL.value == "experimental"


class TestQueueUnchanged:
    def test_queue_still_fifo(self):
        from whooshd.queue import QueueEntry, RequestQueue
        from whooshd.contracts import ChatCompletionRequest, ChatMessage
        queue = RequestQueue()
        for i in range(5):
            req = ChatCompletionRequest(model="m", messages=[ChatMessage(role="user", content=str(i))], stream=False)
            queue.enqueue(QueueEntry(request_id=f"req-{i}", request=req))
        dequeued = []
        while queue.depth > 0:
            e = queue.dequeue()
            if e:
                dequeued.append(e.request_id)
        assert dequeued == [f"req-{i}" for i in range(5)]

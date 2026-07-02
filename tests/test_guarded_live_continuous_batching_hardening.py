"""Hardening tests for guarded live continuous batching prototype — gremlin audit."""

import pytest
from whooshd.continuous_live_prototype import (
    ContinuousLivePrototypeIneligibilityReason,
    ContinuousLivePrototypeReport,
    ContinuousLivePrototypeStatus,
    classify_eligibility,
    run_guarded_prototype,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage, ChatCompletionResponse, ChatCompletionChoice, ChatCompletionUsage


def _req(model="m", stream=False, max_tokens=64, tools=None, **kw):
    return ChatCompletionRequest(model=model, messages=[ChatMessage(role="user", content="hi")], stream=stream, max_tokens=max_tokens, tools=tools, **kw)


class _OkAdapter:
    async def chat_completion_batch(self, requests, **kw):
        import uuid, time
        return [ChatCompletionResponse(id=f"ok-{uuid.uuid4().hex[:6]}", object="chat.completion", created=int(time.time()), model=getattr(r, "model", "m"), choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content="ok"), finish_reason="stop")], usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)) for r in requests]


class _PartialFailAdapter:
    async def chat_completion_batch(self, requests, **kw):
        raise RuntimeError("simulated partial failure")


class _WrongCountAdapter:
    async def chat_completion_batch(self, requests, **kw):
        r = await _OkAdapter().chat_completion_batch(requests[:1])
        return r


class TestDisabledPath:
    def test_disabled_by_default(self):
        r = classify_eligibility(backend="mlx", requests=[_req()])
        assert r == ContinuousLivePrototypeIneligibilityReason.GLOBAL_FLAG_DISABLED

    def test_one_flag_still_disabled(self):
        r = classify_eligibility(backend="mlx", requests=[_req()], global_enabled=True, mlx_enabled=False)
        assert r == ContinuousLivePrototypeIneligibilityReason.MLX_FLAG_DISABLED


class TestMixedEligibility:
    def test_mixed_streaming_rejected(self):
        r = classify_eligibility(backend="mlx", requests=[_req(stream=True), _req(stream=False)], global_enabled=True, mlx_enabled=True)
        assert r == ContinuousLivePrototypeIneligibilityReason.STREAMING_UNSUPPORTED

    def test_mixed_tools_rejected(self):
        r = classify_eligibility(backend="mlx", requests=[_req(), _req(tools=[{"type":"function"}])], global_enabled=True, mlx_enabled=True)
        assert r == ContinuousLivePrototypeIneligibilityReason.TOOL_CALLS_UNSUPPORTED

    def test_max_tokens_exceeded_rejected(self):
        r = classify_eligibility(backend="mlx", requests=[_req(max_tokens=256)], global_enabled=True, mlx_enabled=True, max_tokens=128)
        assert r == ContinuousLivePrototypeIneligibilityReason.MAX_TOKENS_EXCEEDED


class TestFailureModes:
    async def test_partial_failure_no_fallback(self):
        _, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=_PartialFailAdapter())
        assert report.status == ContinuousLivePrototypeStatus.FAILED
        assert report.fallback_after_generation_started is False
        assert report.cleanup_completed is True

    async def test_wrong_count_resolves_all(self):
        responses, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=_WrongCountAdapter())
        assert len(responses) == 2
        assert report.status == ContinuousLivePrototypeStatus.FAILED
        assert report.cleanup_completed is True


class TestResponseShape:
    async def test_success_response_shape(self):
        responses, _ = await run_guarded_prototype(requests=[_req(), _req()], adapter=_OkAdapter())
        for r in responses:
            assert r.object == "chat.completion"
            assert len(r.choices) == 1
            assert r.choices[0].message.content

    async def test_no_prototype_metadata_in_response(self):
        responses, _ = await run_guarded_prototype(requests=[_req(), _req()], adapter=_OkAdapter())
        body = responses[0].model_dump_json()
        for f in ("virtual_slot", "slot_id", "tombstone", "sampling_signature", "chunks_routed", "terminal_events_observed"):
            assert f not in body.lower()


class TestCleanupIdempotent:
    async def test_cleanup_safe_after_failure(self):
        _, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=_PartialFailAdapter())
        assert report.cleanup_completed is True


class TestReportPrivacy:
    async def test_report_no_leaks(self):
        _, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=_OkAdapter())
        s = str(report)
        for f in ("raw_prompt", "token_ids_list", "generated_text_full", "cache_repr", "model_repr", "kv_handle", "traceback"):
            assert f not in s.lower()

    def test_production_ready_false(self):
        r = ContinuousLivePrototypeReport()
        assert r.production_ready is False

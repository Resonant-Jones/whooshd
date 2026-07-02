"""Tests for guarded MLX adapter-batch implementation — forklift in rectangle."""

import pytest
from whooshd.guarded_adapter_batching import (
    GuardedAdapterBatchFailureReason,
    GuardedAdapterBatchIneligibilityReason,
    GuardedAdapterBatchReport,
    GuardedAdapterBatchStatus,
    classify_guard_eligibility,
    run_guarded_adapter_batch,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage, ChatCompletionResponse, ChatCompletionChoice, ChatCompletionUsage


def _req(model="m", stream=False, max_tokens=64, tools=None):
    return ChatCompletionRequest(model=model, messages=[ChatMessage(role="user", content="hi")], stream=stream, max_tokens=max_tokens, tools=tools)


class _OkAdapter:
    async def chat_completion_batch(self, requests, **kw):
        import uuid, time
        return [ChatCompletionResponse(id=f"ok-{uuid.uuid4().hex[:6]}", object="chat.completion", created=int(time.time()), model=getattr(r, "model", "m"), choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content="ok"), finish_reason="stop")], usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)) for r in requests]


class _FailAdapter:
    async def chat_completion_batch(self, requests, **kw):
        raise RuntimeError("fail")


class _WrongCountAdapter:
    async def chat_completion_batch(self, requests, **kw):
        r = await _OkAdapter().chat_completion_batch(requests[:1])
        return r


class TestDisabled:
    def test_disabled_by_default(self):
        r = classify_guard_eligibility("mlx", [_req()])
        assert r == GuardedAdapterBatchIneligibilityReason.GLOBAL_FLAG_DISABLED

    def test_one_flag_still_disabled(self):
        r = classify_guard_eligibility("mlx", [_req()], global_enabled=True, mlx_enabled=False)
        assert r == GuardedAdapterBatchIneligibilityReason.MLX_FLAG_DISABLED


class TestEligibility:
    def test_rejects_streaming(self):
        r = classify_guard_eligibility("mlx", [_req(stream=True)], global_enabled=True, mlx_enabled=True)
        assert r == GuardedAdapterBatchIneligibilityReason.STREAMING_UNSUPPORTED

    def test_rejects_tools(self):
        r = classify_guard_eligibility("mlx", [_req(tools=[{"type":"function"}])], global_enabled=True, mlx_enabled=True)
        assert r == GuardedAdapterBatchIneligibilityReason.TOOL_CALLS_UNSUPPORTED

    def test_rejects_non_mlx(self):
        r = classify_guard_eligibility("stub", [_req()], global_enabled=True, mlx_enabled=True)
        assert r == GuardedAdapterBatchIneligibilityReason.BACKEND_NOT_MLX

    def test_accepts_compatible_group(self):
        r = classify_guard_eligibility("mlx", [_req(), _req()], global_enabled=True, mlx_enabled=True)
        assert r is None


class TestRunner:
    async def test_completes_two_requests(self):
        responses, report = await run_guarded_adapter_batch([_req(), _req()], _OkAdapter())
        assert len(responses) == 2
        assert report.status == GuardedAdapterBatchStatus.COMPLETED
        assert report.request_count == 2
        assert report.virtual_slots_claimed == 2
        assert report.virtual_slots_tombstoned == 2
        assert report.cleanup_completed is True
        assert report.all_slots_tombstoned is True

    async def test_adapter_exception_fails_safely(self):
        responses, report = await run_guarded_adapter_batch([_req(), _req()], _FailAdapter())
        assert report.status == GuardedAdapterBatchStatus.FAILED
        assert report.failure_reason == GuardedAdapterBatchFailureReason.ADAPTER_BATCH_FAILED
        assert report.fallback_after_generation_started is False
        assert report.controlled_errors_emitted == 2
        assert report.cleanup_completed is True
        assert len(responses) == 2

    async def test_wrong_count_fails_safely(self):
        responses, report = await run_guarded_adapter_batch([_req(), _req()], _WrongCountAdapter())
        assert report.status == GuardedAdapterBatchStatus.FAILED
        assert report.controlled_errors_emitted == 2
        assert report.cleanup_completed is True

    async def test_no_fallback_after_generation(self):
        _, report = await run_guarded_adapter_batch([_req(), _req()], _FailAdapter())
        assert report.fallback_after_generation_started is False


class TestResponseShape:
    async def test_openai_compatible(self):
        responses, _ = await run_guarded_adapter_batch([_req(), _req()], _OkAdapter())
        for r in responses:
            assert r.object == "chat.completion"
            assert len(r.choices) == 1
            assert r.choices[0].message.content

    async def test_no_metadata_in_response(self):
        responses, _ = await run_guarded_adapter_batch([_req(), _req()], _OkAdapter())
        body = responses[0].model_dump_json()
        for f in ("virtual_slot", "slot_id", "tombstone", "guarded_adapter", "sampling_signature", "terminal_events"):
            assert f not in body.lower()


class TestReportPrivacy:
    async def test_report_metadata_only(self):
        _, report = await run_guarded_adapter_batch([_req(), _req()], _OkAdapter())
        s = str(report)
        for f in ("raw_prompt", "token_ids_list", "generated_text_full", "cache_repr", "model_repr", "kv_handle", "traceback"):
            assert f not in s.lower()

    def test_not_production(self):
        assert GuardedAdapterBatchReport().production_ready is False
        assert GuardedAdapterBatchReport().token_step_scheduler is False

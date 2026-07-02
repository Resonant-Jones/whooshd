"""Tests for guarded live continuous batching prototype — sandbagged bunker."""

import pytest
from whooshd.continuous_live_prototype import (
    ContinuousLivePrototypeIneligibilityReason,
    ContinuousLivePrototypeReport,
    ContinuousLivePrototypeStatus,
    classify_eligibility,
    run_guarded_prototype,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage


def _req(model="m", stream=False, max_tokens=64):
    return ChatCompletionRequest(model=model, messages=[ChatMessage(role="user", content="hi")], stream=stream, max_tokens=max_tokens)


class _FakeBatchAdapter:
    def __init__(self, texts=None):
        self.texts = texts or ["hello", "world"]

    async def chat_completion_batch(self, requests, **kwargs):
        from whooshd.contracts import ChatCompletionResponse, ChatCompletionChoice, ChatCompletionUsage, ChatMessage
        import uuid, time
        return [
            ChatCompletionResponse(
                id=f"fb-{uuid.uuid4().hex[:6]}", object="chat.completion",
                created=int(time.time()), model=getattr(r, "model", "m"),
                choices=[ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content=self.texts[i]), finish_reason="stop")],
                usage=ChatCompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            for i, r in enumerate(requests)
        ]


class TestEligibility:
    def test_disabled_by_default(self):
        r = classify_eligibility(backend="mlx", requests=[_req()])
        assert r == ContinuousLivePrototypeIneligibilityReason.GLOBAL_FLAG_DISABLED

    def test_both_flags_required(self):
        r = classify_eligibility(backend="mlx", requests=[_req()], global_enabled=True, mlx_enabled=False)
        assert r == ContinuousLivePrototypeIneligibilityReason.MLX_FLAG_DISABLED

    def test_mlx_only(self):
        r = classify_eligibility(backend="stub", requests=[_req()], global_enabled=True, mlx_enabled=True)
        assert r == ContinuousLivePrototypeIneligibilityReason.BACKEND_NOT_MLX

    def test_streaming_rejected(self):
        r = classify_eligibility(backend="mlx", requests=[_req(stream=True)], global_enabled=True, mlx_enabled=True)
        assert r == ContinuousLivePrototypeIneligibilityReason.STREAMING_UNSUPPORTED

    def test_tool_calls_rejected(self):
        req = ChatCompletionRequest(model="m", messages=[ChatMessage(role="user", content="hi")], tools=[{"type": "function"}])
        r = classify_eligibility(backend="mlx", requests=[req], global_enabled=True, mlx_enabled=True)
        assert r == ContinuousLivePrototypeIneligibilityReason.TOOL_CALLS_UNSUPPORTED

    def test_group_too_small(self):
        r = classify_eligibility(backend="mlx", requests=[_req()], global_enabled=True, mlx_enabled=True, min_group=2)
        assert r == ContinuousLivePrototypeIneligibilityReason.GROUP_TOO_SMALL

    def test_group_too_large(self):
        r = classify_eligibility(backend="mlx", requests=[_req(), _req(), _req()], global_enabled=True, mlx_enabled=True, max_group=2)
        assert r == ContinuousLivePrototypeIneligibilityReason.GROUP_TOO_LARGE

    def test_eligible_2_request_group(self):
        r = classify_eligibility(backend="mlx", requests=[_req(), _req()], global_enabled=True, mlx_enabled=True)
        assert r is None


class TestPrototypeRunner:
    async def test_completes_two_requests(self):
        adapter = _FakeBatchAdapter()
        responses, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=adapter)
        assert len(responses) == 2
        assert report.status == ContinuousLivePrototypeStatus.COMPLETED
        assert report.request_count == 2
        assert report.virtual_slots_claimed == 2
        assert report.virtual_slots_tombstoned == 2
        assert report.terminal_events_observed == 2
        assert report.cleanup_completed is True

    async def test_wrong_response_count_handled(self):
        class _Bad(_FakeBatchAdapter):
            async def chat_completion_batch(self, requests, **kwargs):
                r = await super().chat_completion_batch(requests[:1], **kwargs)
                return r

        adapter = _Bad()
        responses, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=adapter)
        assert report.status == ContinuousLivePrototypeStatus.FAILED
        assert report.fallback_after_generation_started is False
        assert report.live_path_enabled is True


class TestReportPrivacy:
    async def test_report_metadata_only(self):
        adapter = _FakeBatchAdapter()
        _, report = await run_guarded_prototype(requests=[_req(), _req()], adapter=adapter)
        s = str(report)
        for f in ("raw_prompt", "token_ids_list", "generated_text_full", "cache_repr",
                   "model_repr", "kv_handle", "traceback"):
            assert f not in s.lower()

    def test_report_not_production(self):
        r = ContinuousLivePrototypeReport()
        assert r.production_ready is False


class TestExistingPathsUnchanged:
    def test_eligibility_disabled_by_default(self):
        """Without flags, nothing changes."""
        r = classify_eligibility(backend="mlx", requests=[_req()])
        assert r is not None  # Ineligible by default.

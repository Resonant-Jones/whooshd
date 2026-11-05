"""End-to-end regression tests for the per-model context policy.

These exercise the complete path — request → route resolution → budget
selection → costing → admission decision → HTTP response — because the
defects lived in the *integration*, not in any single component:

1. Budgets were selected by the **process-wide** controller identity, so a
   request routed to model B was measured against model A's hard ceiling.
2. ``max_completion_tokens`` was not reserved, so a client asking for a
   large completion limit was charged the *default* output reservation.
3. Forwarded prompt-affecting fields (tool definitions, assistant tool
   calls, structured-output schemas) were not counted at all, so a request
   carrying a large tool set passed a hard budget it could never fit.
4. The profile's declared per-direction maxima were never enforced.
5. ``/v1/generate`` never consulted the context policy at all.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from whooshd.capacity_profile import ContextBudgets
from whooshd.contracts import ChatCompletionResponse, ChatCompletionUsage
from whooshd.runtime import RuntimeState


class _ModelAdapter:
    """Minimal adapter that reports a resolvable model id."""

    def __init__(self, kind: str, model_id: str):
        self._kind = kind
        self._model_id = model_id

    @property
    def name(self) -> str:
        return self._kind

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def max_concurrent_requests(self) -> int | None:
        return 2

    @property
    def supports_streaming(self) -> bool:
        return False

    def is_loaded(self) -> bool:
        return True

    def model_id(self) -> str | None:
        return self._model_id

    async def warmup(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def health(self):  # pragma: no cover - not exercised here
        raise NotImplementedError

    async def chat_completion(
        self, request, context=None
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id="chatcmpl-ctx",
            created=0,
            model=self._model_id,
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            ),
        )

    async def chat_completion_stream(self, request, context=None):  # pragma: no cover
        raise NotImplementedError
        yield  # pragma: no cover

    async def generate(self, request, context=None):  # pragma: no cover
        raise NotImplementedError


def _write_profile(tmp_path, monkeypatch, *, model_id: str, budgets):
    """Install a capacity profile for *model_id* and point the broker at it."""
    from whooshd.capacity_profile import CapacityProfile

    profile = CapacityProfile(
        model_id=model_id,
        runtime="lane_a",
        machine_class="mc",
        prompt_size_chars=100,
        configured_max_tokens=256,
        context_budgets=budgets,
    )
    path = tmp_path / "profile.json"
    path.write_text(profile.model_dump_json())

    monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", str(path))
    monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", model_id)
    monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "lane_a")
    monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "mc")
    return path


@pytest.fixture
def context_deployment(monkeypatch):
    """A router with two models: one with budgets, one without."""
    from whooshd import app as app_module
    from whooshd.routing import RuntimeRouter

    budgeted = _ModelAdapter("lane_a", "model-a")
    unbudgeted = _ModelAdapter("lane_b", "model-b")

    router = RuntimeRouter()
    router.register(budgeted)
    router.register(unbudgeted)
    monkeypatch.setattr(app_module, "get_router", lambda: router)

    runtime = RuntimeState()
    monkeypatch.setattr(app_module, "get_runtime", lambda: runtime)

    monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "4")
    monkeypatch.setenv("WHOOSHD_ENABLE_QUEUE", "true")
    monkeypatch.setenv("WHOOSHD_MAX_QUEUE_DEPTH", "8")
    monkeypatch.setenv("WHOOSHD_QUEUE_TIMEOUT_SECONDS", "5")

    return {
        "router": router,
        "runtime": runtime,
        "budgeted": budgeted,
        "unbudgeted": unbudgeted,
    }


class TestBudgetsAreSelectedByTheResolvedModel:
    @pytest.mark.asyncio
    async def test_a_model_without_budgets_is_not_constrained(
        self, context_deployment, tmp_path, monkeypatch
    ):
        """Model B must not inherit model A's hard ceiling."""
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=100),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-b",
                    "messages": [
                        {"role": "user", "content": "x" * 4000}
                    ],
                    "stream": False,
                },
                timeout=30,
            )

        # Model B has no measured budgets: it is admitted unchanged rather
        # than refused against another model's ceiling.
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_the_budgeted_model_is_still_constrained(
        self, context_deployment, tmp_path, monkeypatch
    ):
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=100),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-a",
                    "messages": [
                        {"role": "user", "content": "x" * 4000}
                    ],
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "context_overflow"


class TestForwardedPromptFieldsAreCharged:
    @pytest.mark.asyncio
    async def test_a_large_tool_set_breaches_the_hard_budget(
        self, context_deployment, tmp_path, monkeypatch
    ):
        """Tool definitions are forwarded upstream, so they are charged."""
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=1000),
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"tool_{i}",
                    "description": "d" * 2000,
                    "parameters": {"type": "object"},
                },
            }
            for i in range(8)
        ]

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": tools,
                    "max_tokens": 64,
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        assert resp.json()["code"] == "context_overflow"


class TestCompletionTokenLimitsAreReserved:
    @pytest.mark.asyncio
    async def test_max_completion_tokens_is_charged(
        self, context_deployment, tmp_path, monkeypatch
    ):
        """A large ``max_completion_tokens`` must not be charged as 256."""
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=1000),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_completion_tokens": 8000,
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        assert resp.json()["code"] == "context_overflow"


class TestDeclaredPerDirectionMaxima:
    @pytest.mark.asyncio
    async def test_input_above_the_declared_maximum_is_refused(
        self, context_deployment, tmp_path, monkeypatch
    ):
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(
                hard_max_tokens=32000, max_input_tokens=100
            ),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "x" * 4000}],
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        assert resp.json()["code"] == "context_overflow"

    @pytest.mark.asyncio
    async def test_output_above_the_declared_maximum_is_refused(
        self, context_deployment, tmp_path, monkeypatch
    ):
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(
                hard_max_tokens=32000, max_output_tokens=1024
            ),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 8000,
                    "stream": False,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        assert resp.json()["code"] == "context_overflow"


class TestGeneratePathAppliesTheContextPolicy:
    @pytest.mark.asyncio
    async def test_generate_above_the_hard_budget_is_refused(
        self, context_deployment, tmp_path, monkeypatch
    ):
        """``/v1/generate`` must apply the same hard policy as chat."""
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=100),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/generate",
                json={
                    "model_id": "model-a",
                    "prompt": "x" * 4000,
                    "max_tokens": 16,
                },
                timeout=30,
            )

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "context_overflow"

    @pytest.mark.asyncio
    async def test_generate_below_the_hard_budget_is_admitted(
        self, context_deployment, tmp_path, monkeypatch
    ):
        _write_profile(
            tmp_path,
            monkeypatch,
            model_id="model-a",
            budgets=ContextBudgets(hard_max_tokens=32000),
        )

        from whooshd.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/generate",
                json={"model_id": "model-a", "prompt": "hi", "max_tokens": 4},
                timeout=30,
            )

        # The stub/generate adapter raises NotImplementedError in this
        # harness; what matters is that the request was *not* refused by
        # the context policy.
        assert resp.status_code != 422

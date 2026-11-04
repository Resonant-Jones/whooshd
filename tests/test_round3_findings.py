"""Regression tests for the three findings raised against ``94c9049``.

1. **P1 — model identity matching conflated repositories.**  The basename
   fallback treated ``org-a/foo`` and ``org-b/foo`` as the same model, so
   one model's context ceiling could be applied to another.

2. **P2 — per-request throughput used band wall time.**  Dividing a band's
   average token count by the band's wall clock underreports every request
   that finishes faster than the slowest one.

3. **P2 — an invalid profile was re-parsed on every request.**  A malformed
   or schema-invalid profile was re-read and re-validated on the request
   path, because the cache only reused a *successful* load.

The tests are hermetic: no network, no Metal, no live broker.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from whooshd import capacity_controller as controller_module
from whooshd.capacity_controller import (
    CapacityController,
    model_identity_matches,
)
from whooshd.capacity_profile import CapacityProfile, ContextBudgets

_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import lane_concurrency_experiment as harness  # noqa: E402


# ── Finding 1 — repository ownership in model-budget matching ──────────────


class TestModelIdentityKeepsRepositoryOwnership:
    def test_two_repositories_with_the_same_basename_are_not_the_same_model(self):
        """The reviewer's scenario.

        A profile for ``org-a/foo`` must not be applied to a request
        resolved to ``org-b/foo``.
        """
        assert model_identity_matches("org-a/foo", "org-b/foo") is False

    def test_the_same_repository_with_the_same_basename_matches(self):
        assert model_identity_matches("org-a/foo", "org-a/foo") is True

    def test_a_local_snapshot_of_one_repo_does_not_match_another_repo(self):
        """The snapshot layout carries the real repository name."""
        assert (
            model_identity_matches(
                "/models/models--org-a--foo", "org-b/foo"
            )
            is False
        )
        assert (
            model_identity_matches(
                "/models/models--org-a--foo", "org-a/foo"
            )
            is True
        )

    def test_a_local_snapshot_path_matches_its_repository_id(self):
        assert (
            model_identity_matches(
                "/models/models--mlx-community--gemma-4-12B-it-qat-4bit",
                "mlx-community/gemma-4-12B-it-qat-4bit",
            )
            is True
        )

    def test_a_repository_id_does_not_match_a_bare_name(self):
        """Without a namespace on the other side, equality cannot be shown."""
        assert model_identity_matches("org-a/foo", "foo") is False
        assert model_identity_matches("foo", "org-a/foo") is False

    def test_two_bare_names_match(self):
        assert model_identity_matches("foo", "foo") is True
        assert model_identity_matches("Foo", "foo") is True

    def test_empty_identities_never_match(self):
        assert model_identity_matches("", "foo") is False
        assert model_identity_matches("foo", "") is False

    def test_a_profile_for_one_repo_does_not_constrain_another(self, tmp_path, monkeypatch):
        """End to end: the wrong budget must not be applied."""
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", "org-a/foo")
        monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "mlx_vlm")
        monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "mc")

        profile = CapacityProfile(
            model_id="org-a/foo",
            runtime="mlx_vlm",
            machine_class="mc",
            prompt_size_chars=10,
            configured_max_tokens=16,
            context_budgets=ContextBudgets(hard_max_tokens=100),
        )
        path = tmp_path / "profile.json"
        path.write_text(profile.model_dump_json())

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()

        assert controller.context_budgets(model_id="org-a/foo") is not None
        # The same basename in a different repository is a different model.
        assert controller.context_budgets(model_id="org-b/foo") is None


# ── Finding 2 — per-request throughput uses each request's own latency ─────


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Fake client whose requests have distinct latencies and token counts."""

    broker = None

    def __init__(self, *args, **kwargs):
        self._broker = type(self).broker

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, timeout=None):
        body = {
            "max_active_requests": 2,
            "active_jobs": 0,
            "queue_enabled": True,
            "queue_depth": 0,
            "capacity": {"lane": None, "lane_max_concurrent": None},
        }
        return _FakeResponse(200, body)

    async def post(self, url, json=None, timeout=None):
        broker = self._broker
        broker.in_flight += 1
        try:
            index = broker.chat_posts
            broker.chat_posts += 1
            await asyncio.sleep(broker.delays[index % len(broker.delays)])
            tokens = broker.tokens[index % len(broker.tokens)]
            return _FakeResponse(
                200, {"usage": {"prompt_tokens": 10, "completion_tokens": tokens}}
            )
        finally:
            broker.in_flight -= 1


class _FakeBroker:
    def __init__(self, delays, tokens):
        self.delays = delays
        self.tokens = tokens
        self.chat_posts = 0
        self.in_flight = 0


@pytest.mark.asyncio
async def test_per_request_rate_is_the_mean_of_each_requests_own_rate(monkeypatch):
    """The reviewer's scenario.

    One request finishes fast and generates few tokens; the other is slow.
    The band's wall clock is the slow request's duration, so a wall-time
    division underreports the fast one.
    """
    broker = _FakeBroker(delays=[0.05, 0.20], tokens=[10, 10])
    monkeypatch.setattr(
        _FakeAsyncClient, "broker", broker, raising=False
    )

    band = await harness.run_band(_FakeAsyncClient(), concurrency=2)

    # Each request: 10 tokens over its own latency.
    expected_rates = [
        10 / 0.05,
        10 / 0.20,
    ]
    expected = sum(expected_rates) / len(expected_rates)

    assert band["per_request_tokens_per_second"] == pytest.approx(
        expected, rel=0.2
    )

    # The wall-time figure would be materially lower: it divides by the
    # slowest request's duration.
    wall = band["wall_seconds"]
    wall_based = (sum(broker.tokens[:2]) / 2) / wall
    assert wall_based < band["per_request_tokens_per_second"]

    # Aggregate throughput is unchanged: it is genuinely band wall time.
    assert band["aggregate_tokens_per_second"] == pytest.approx(
        sum(broker.tokens[:2]) / wall, rel=0.05
    )


@pytest.mark.asyncio
async def test_a_single_request_band_is_unchanged(monkeypatch):
    broker = _FakeBroker(delays=[0.05], tokens=[10])
    monkeypatch.setattr(
        _FakeAsyncClient, "broker", broker, raising=False
    )
    band = await harness.run_band(_FakeAsyncClient(), concurrency=1)
    assert band["per_request_tokens_per_second"] == pytest.approx(
        10 / 0.05, rel=0.2
    )


# ── Finding 3 — an invalid profile is not re-parsed on every request ──────


class TestInvalidProfileIsCached:
    def test_a_malformed_profile_is_read_once(self, tmp_path, monkeypatch):
        path = tmp_path / "profile.json"
        path.write_text("{ this is not json")

        calls = {"n": 0}
        original = controller_module.safe_load_profile

        def counting_loader(p):
            calls["n"] += 1
            return original(p)

        monkeypatch.setattr(controller_module, "safe_load_profile", counting_loader)

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()

        assert controller.context_budgets() is None
        first = calls["n"]
        assert first == 1

        # Subsequent request-path lookups must not re-read the file.
        for _ in range(5):
            assert controller.context_budgets() is None
        assert calls["n"] == first, "the broken profile was re-parsed"

    def test_a_schema_invalid_profile_is_read_once(self, tmp_path, monkeypatch):
        path = tmp_path / "profile.json"
        path.write_text('{"model_id": "m"}')  # missing required fields

        calls = {"n": 0}
        original = controller_module.safe_load_profile

        def counting_loader(p):
            calls["n"] += 1
            return original(p)

        monkeypatch.setattr(controller_module, "safe_load_profile", counting_loader)

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()

        assert controller.context_budgets() is None
        assert calls["n"] == 1
        for _ in range(3):
            assert controller.context_budgets() is None
        assert calls["n"] == 1

    def test_a_fixed_profile_is_picked_up_again(self, tmp_path, monkeypatch):
        path = tmp_path / "profile.json"
        path.write_text("{ this is not json")

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()
        assert controller._get_profile() is None

        # The operator repairs the file.  The cached failure must not mask
        # the corrected profile.
        profile = CapacityProfile(
            model_id="m",
            runtime="mlx_vlm",
            machine_class="mc",
            prompt_size_chars=10,
            configured_max_tokens=16,
            context_budgets=ContextBudgets(hard_max_tokens=100),
        )
        path.write_text(profile.model_dump_json())
        os.utime(path, (time.time() + 5, time.time() + 5))

        controller.invalidate_profile_cache()
        assert controller._get_profile() is not None

    def test_invalidating_the_cache_retries_a_broken_profile(self, tmp_path, monkeypatch):
        path = tmp_path / "profile.json"
        path.write_text("{ this is not json")

        calls = {"n": 0}
        original = controller_module.safe_load_profile

        def counting_loader(p):
            calls["n"] += 1
            return original(p)

        monkeypatch.setattr(controller_module, "safe_load_profile", counting_loader)

        controller = CapacityController(profile_path=str(path))
        controller.invalidate_profile_cache()
        assert controller.context_budgets() is None
        assert calls["n"] == 1

        controller.invalidate_profile_cache()
        assert controller.context_budgets() is None
        assert calls["n"] == 2, "the failure cache was not cleared"

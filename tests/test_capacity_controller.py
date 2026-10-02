"""Tests for the capacity controller.

Covers:
  * Behaviour parity in fixed mode (the default).
  * Adaptive mode honours the operator ceiling.
  * Missing / corrupt profile falls back conservatively.
  * Memory pressure reduces or denies new admission.
  * Snapshot exposes safe, structured reasons.

No I/O.  No asyncio.  Pure controller logic.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from whooshd.admission import reset_controller_for_tests
from whooshd.capacity_controller import (
    CapacityAction,
    CapacityContext,
    CapacityController,
)
from whooshd.capacity_profile import (
    CapacityBand,
    CapacityProfile,
    MemoryPressureClass,
    safe_save_profile,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Clear env vars between tests so the controller starts fresh."""
    monkeypatch.delenv("WHOOSHD_CAPACITY_MODE", raising=False)
    monkeypatch.delenv("WHOOSHD_CAPACITY_PROFILE_PATH", raising=False)
    monkeypatch.delenv("WHOOSHD_CAPACITY_MEMORY_PRESSURE_DENY", raising=False)
    monkeypatch.delenv("WHOOSHD_MAX_ACTIVE_REQUESTS", raising=False)
    monkeypatch.setenv("WHOOSHD_CAPACITY_MODEL_ID", "m")
    monkeypatch.setenv("WHOOSHD_CAPACITY_RUNTIME", "mlx")
    monkeypatch.setenv("WHOOSHD_CAPACITY_MACHINE_CLASS", "darwin-arm64")
    reset_controller_for_tests()
    yield
    reset_controller_for_tests()


def _ctx(**kwargs) -> CapacityContext:
    return CapacityContext(
        active_jobs=kwargs.get("active_jobs", 0),
        queue_depth=kwargs.get("queue_depth", 0),
        max_queue_depth=kwargs.get("max_queue_depth", 8),
        runtime_ready=kwargs.get("runtime_ready", True),
        memory_pressure=kwargs.get("memory_pressure", MemoryPressureClass.UNKNOWN),
    )


def _save_profile(profile: CapacityProfile) -> str:
    """Save to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    safe_save_profile(profile, path)
    return path


def _band(concurrency: int, *, successful: bool = True) -> CapacityBand:
    return CapacityBand.model_validate({
        "concurrency": concurrency,
        "successful": successful,
        "request_count": 8,
        "latency_p50_ms": 1500.0,
        "latency_p95_ms": 2500.0,
        "success_count": 8 if successful else 7,
        "overload_count": 0 if successful else 1,
        "failure_count": 0,
        "stuck_count": 0,
        "memory_pressure": "normal",
    })


# ── Fixed mode parity ──────────────────────────────────────────────────────


class TestFixedModeParity:
    def test_default_is_fixed_mode(self, monkeypatch):
        # Default mode is fixed regardless of construction order.
        monkeypatch.delenv("WHOOSHD_CAPACITY_MODE", raising=False)
        cc = CapacityController()
        assert cc.mode.value == "fixed"

    def test_fixed_mode_uses_operator_ceiling(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "3")
        cc = Control()
        assert cc.operator_ceiling == 3
        assert cc.effective_active_limit() == 3

    def test_fixed_mode_evaluates_run_under_limit(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
        cc = Control()
        e = cc.evaluate(_ctx(active_jobs=0))
        assert e.action == CapacityAction.RUN
        assert e.effective_active_limit == 2
        assert e.operator_ceiling == 2

    def test_fixed_mode_evaluates_queue_at_limit(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
        cc = Control()
        e = cc.evaluate(_ctx(active_jobs=2))
        assert e.action == CapacityAction.QUEUE

    def test_fixed_mode_evaluates_reject_when_queue_full(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "1")
        cc = Control()
        e = cc.evaluate(_ctx(active_jobs=1, queue_depth=8, max_queue_depth=8))
        assert e.action == CapacityAction.REJECT


# ── Adaptive mode ceiling enforcement ──────────────────────────────────────


class TestAdaptiveCeiling:
    def test_adaptive_does_not_exceed_operator_ceiling(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "4")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(1), _band(2), _band(3), _band(4), _band(6), _band(8)),
            "recommended_active_concurrency": 8,  # calibrated high
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)

        cc = Control()
        # Operator ceiling is 4.  Profile says 8.  Effective must be 4.
        assert cc.effective_active_limit() == 4
        assert cc.operator_ceiling == 4

    def test_adaptive_can_lower_below_ceiling(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "8")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(1), _band(2)),
            "recommended_active_concurrency": 2,  # calibrated low
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)

        cc = Control()
        # Operator ceiling is 8.  Profile says 2.  Effective is 2.
        assert cc.effective_active_limit() == 2

    def test_adaptive_run_under_calibrated_limit(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "8")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(1), _band(2)),
            "recommended_active_concurrency": 4,
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)
        cc = Control()

        e = cc.evaluate(_ctx(active_jobs=1))
        assert e.action == CapacityAction.RUN
        assert e.calibrated_concurrency == 4
        # Reason is CALIBRATED_LIMIT because profile lowers the effective ceiling.
        assert e.reason.value == "calibrated_limit"


# ── Profile fallback ───────────────────────────────────────────────────────


class TestProfileFallback:
    def test_missing_profile_falls_back_to_ceiling(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "3")
        # No profile path set.
        cc = Control()
        assert cc.effective_active_limit() == 3
        e = cc.evaluate(_ctx(active_jobs=0))
        assert e.action == CapacityAction.RUN
        assert e.profile_loaded is False

    def test_invalid_profile_falls_back_safely(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "3")
        # Create an invalid JSON file.
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w") as f:
            f.write("not valid json")
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)

        cc = Control()
        # Must not crash.  Must fall back to operator ceiling.
        assert cc.effective_active_limit() == 3
        e = cc.evaluate(_ctx(active_jobs=0))
        assert e.action == CapacityAction.RUN
        assert e.profile_loaded is False

    def test_corrupt_profile_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "3")
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        # JSON but missing required fields.
        with open(path, "w") as f:
            json.dump({"completely": "wrong"}, f)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)

        cc = Control()
        # Falls back silently.
        assert cc.effective_active_limit() == 3

    @pytest.mark.parametrize(
        ("env_name", "env_value", "reason"),
        [
            ("WHOOSHD_CAPACITY_MODEL_ID", "other-model", "model_mismatch"),
            ("WHOOSHD_CAPACITY_RUNTIME", "other-runtime", "runtime_mismatch"),
            ("WHOOSHD_CAPACITY_MACHINE_CLASS", "other-machine", "machine_mismatch"),
        ],
    )
    def test_identity_mismatch_ignores_profile_and_uses_operator_ceiling(
        self, monkeypatch, env_name, env_value, reason
    ):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "6")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "recommended_active_concurrency": 2,
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)
        monkeypatch.setenv(env_name, env_value)

        cc = Control()
        evaluation = cc.evaluate(_ctx())

        assert evaluation.effective_active_limit == 6
        assert evaluation.profile_loaded is True
        assert evaluation.profile_eligible is False
        assert evaluation.profile_rejection_reason == reason

    def test_host_memory_mismatch_ignores_profile(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "6")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "host_memory_bytes": 1,
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "recommended_active_concurrency": 2,
        })
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", _save_profile(profile))
        monkeypatch.setattr(
            CapacityController,
            "_host_memory_bytes",
            staticmethod(lambda: 2),
        )

        evaluation = CapacityController().evaluate(_ctx())

        assert evaluation.effective_active_limit == 6
        assert evaluation.profile_eligible is False
        assert evaluation.profile_rejection_reason == "host_memory_mismatch"


# ── Memory pressure ────────────────────────────────────────────────────────


class TestMemoryPressure:
    def test_high_memory_pressure_reduces_limit_adaptive(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "8")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(2), _band(4)),
            "recommended_active_concurrency": 4,
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)
        cc = Control()

        normal = cc.effective_active_limit(memory_pressure=MemoryPressureClass.NORMAL)
        high = cc.effective_active_limit(memory_pressure=MemoryPressureClass.HIGH)
        assert normal == 4
        assert high < normal  # reduced

    def test_high_memory_pressure_deny_denies_new_admission(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "4")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(1),),
            "recommended_active_concurrency": 4,  # → effective 4 under normal
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)
        cc = Control()

        # Under HIGH pressure the effective limit halves: 4 → 2.
        # active_jobs=1, effective limit=2 → 2 ≤ 1+1 → deny with QUEUE.
        e = cc.evaluate(_ctx(
            active_jobs=1,
            memory_pressure=MemoryPressureClass.HIGH,
        ))
        assert e.action == CapacityAction.QUEUE
        assert e.reason.value == "memory_pressure_high"

    def test_normal_memory_pressure_admits(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "fixed")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
        cc = Control()
        e = cc.evaluate(_ctx(
            active_jobs=1,
            memory_pressure=MemoryPressureClass.NORMAL,
        ))
        assert e.action == CapacityAction.RUN

    def test_critical_memory_pressure_reduces_further(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_CAPACITY_MODE", "adaptive")
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "8")
        profile = CapacityProfile.model_validate({
            "model_id": "m",
            "runtime": "mlx",
            "machine_class": "darwin-arm64",
            "prompt_size_chars": 64,
            "configured_max_tokens": 128,
            "streaming": False,
            "bands": (_band(4),),
            "recommended_active_concurrency": 4,
        })
        path = _save_profile(profile)
        monkeypatch.setenv("WHOOSHD_CAPACITY_PROFILE_PATH", path)
        cc = Control()

        # Critical pressure should still reduce but never below 1.
        limit = cc.effective_active_limit(memory_pressure=MemoryPressureClass.CRITICAL)
        assert limit >= 1
        assert limit < 4


# ── Snapshot ───────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_exposes_required_keys(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "4")
        cc = Control()
        snap = cc.snapshot(
            active_jobs=1,
            queue_depth=2,
            max_queue_depth=8,
            runtime_ready=True,
        )
        for k in (
            "capacity_mode", "effective_active_limit", "operator_ceiling",
            "active_jobs", "queue_depth", "max_queue_depth",
            "capacity_reason", "memory_pressure",
        ):
            assert k in snap, f"missing key: {k}"

    def test_snapshot_has_no_prompt_or_token_leakage(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_MAX_ACTIVE_REQUESTS", "2")
        cc = Control()
        snap = cc.snapshot(
            active_jobs=0,
            queue_depth=0,
            max_queue_depth=8,
            runtime_ready=True,
        )
        data = str(snap)
        for forbidden in ("prompt", "messages", "content", "tokens", "kv", "threadwake_cache_ready"):
            assert forbidden not in data, f"leaked: {forbidden}"


# ── Helper class that uses the env-correct mode ───────────────────────────


class Control:
    """Wrapper that re-reads the mode after env vars are set in tests."""

    def __init__(self) -> None:
        self._impl = CapacityController()

    def evaluate(self, ctx):
        return self._impl.evaluate(ctx)

    @property
    def mode(self):
        return self._impl.mode

    @property
    def operator_ceiling(self):
        return self._impl.operator_ceiling

    def effective_active_limit(self, **kwargs):
        return self._impl.effective_active_limit(**kwargs)

    def snapshot(self, **kwargs):
        return self._impl.snapshot(**kwargs)

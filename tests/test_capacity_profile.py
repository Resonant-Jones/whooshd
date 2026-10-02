"""Tests for the capacity profile artifact and safe loaders."""

from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest

from whooshd.capacity_profile import (
    CapacityBand,
    CapacityMode,
    CapacityProfile,
    MemoryPressureClass,
    safe_load_profile,
    safe_save_profile,
)


def _band(concurrency: int, *, successful: bool = True, **kwargs) -> CapacityBand:
    return CapacityBand.model_validate(
        {
            "concurrency": concurrency,
            "successful": successful,
            "request_count": 8,
            "ttft_p50_ms": 100.0,
            "ttft_p95_ms": 200.0,
            "latency_p50_ms": 1500.0,
            "latency_p95_ms": 2500.0,
            "success_count": 8 if successful else 7,
            "overload_count": 0 if successful else 1,
            "failure_count": 0,
            "stuck_count": 0,
            "memory_pressure": MemoryPressureClass.NORMAL.value,
            **kwargs,
        }
    )


def _profile(*, recommended: int = 2, **kwargs) -> CapacityProfile:
    payload = {
        "model_id": "m",
        "runtime": "stub",
        "machine_class": "darwin-arm64",
        "host_memory_bytes": 34_359_738_368,
        "prompt_size_chars": 64,
        "configured_max_tokens": 128,
        "streaming": True,
        "bands": (
            _band(1).model_dump(mode="json"),
            _band(2, successful=True).model_dump(mode="json"),
        ),
        "recommended_active_concurrency": recommended,
    }
    payload.update(kwargs)
    return CapacityProfile.model_validate(payload)


class TestCapacityBand:
    def test_required_concurrency(self):
        with pytest.raises(ValueError):
            CapacityBand(concurrency=0)

    def test_all_optional_fields_default_to_none_or_zero(self):
        b = CapacityBand(concurrency=1, successful=True)
        assert b.ttft_p50_ms is None
        assert b.aggregate_tokens_per_second is None
        assert b.success_count == 0


class TestCapacityProfile:
    def test_recommended_must_be_positive(self):
        with pytest.raises(ValueError):
            _profile(recommended=0)

    def test_persistence_round_trip(self):
        profile = _profile()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cap.json")
            assert safe_save_profile(profile, path) is True
            loaded = safe_load_profile(path)
            assert loaded is not None
            assert loaded.model_id == profile.model_id
            assert loaded.recommended_active_concurrency == profile.recommended_active_concurrency
            assert len(loaded.bands) == len(profile.bands)
            assert loaded.bands[0].concurrency == 1
            assert loaded.bands[1].concurrency == 2

    def test_safe_load_returns_none_on_missing_file(self):
        assert safe_load_profile("/tmp/does-not-exist-" + uuid.uuid4().hex) is None

    def test_safe_load_returns_none_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad.json")
            with open(path, "w") as f:
                f.write("{ not json")
            assert safe_load_profile(path) is None

    def test_safe_load_returns_none_on_schema_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bad-schema.json")
            with open(path, "w") as f:
                json.dump({"this_is": "not_a_profile"}, f)
            assert safe_load_profile(path) is None

    def test_snapshot_does_not_leak(self):
        """Profile fields must never contain prompt / generated content."""
        profile = _profile()
        # The model itself only carries safe metadata fields.
        data = profile.model_dump(mode="json")
        for forbidden in ("prompt", "messages", "content", "text", "tokens", "output"):
            assert forbidden not in data


class TestSafeLoadEmptyPath:
    def test_empty_string_returns_none(self):
        assert safe_load_profile("") is None
        assert safe_load_profile(None) is None  # type: ignore[arg-type]
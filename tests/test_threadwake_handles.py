"""Tests for KVHandle and KVCapability."""

from __future__ import annotations

import json

import pytest

from whooshd.runtime.threadwake.handles import KVCapability, KVHandle


class TestKVHandle:
    def test_construction_defaults(self):
        handle = KVHandle(backend="stub", model_id="test-model")
        assert handle.id.startswith("kv-")
        assert handle.backend == "stub"
        assert handle.model_id == "test-model"
        assert handle.token_count == 0
        assert handle.scope == "thread"
        assert handle.metadata == {}
        assert handle.opaque_ref is None

    def test_custom_values(self):
        handle = KVHandle(
            id="my-id",
            backend="mlx",
            model_id="llama-3",
            token_count=42,
            scope="user",
            metadata={"key": "value"},
            opaque_ref={"internal": "data"},
        )
        assert handle.id == "my-id"
        assert handle.backend == "mlx"
        assert handle.model_id == "llama-3"
        assert handle.token_count == 42
        assert handle.scope == "user"
        assert handle.metadata == {"key": "value"}
        assert handle.opaque_ref == {"internal": "data"}

    def test_opaque_ref_excluded_from_model_dump(self):
        handle = KVHandle(
            backend="mlx",
            model_id="test",
            opaque_ref={"secret": "kv-state"},
        )
        dumped = handle.model_dump()
        assert "opaque_ref" not in dumped

    def test_opaque_ref_excluded_from_model_dump_json(self):
        handle = KVHandle(
            backend="mlx",
            model_id="test",
            opaque_ref=b"\x00\x01\x02\x03",
        )
        json_str = handle.model_dump_json()
        parsed = json.loads(json_str)
        assert "opaque_ref" not in parsed

    def test_public_snapshot_excludes_opaque_ref(self):
        handle = KVHandle(
            backend="mlx",
            model_id="test",
            opaque_ref="sensitive",
        )
        snapshot = handle.public_snapshot()
        assert "opaque_ref" not in snapshot
        assert snapshot["backend"] == "mlx"
        assert snapshot["model_id"] == "test"

    def test_public_snapshot_includes_all_public_fields(self):
        handle = KVHandle(
            id="test-id",
            backend="stub",
            model_id="model",
            token_count=10,
            scope="thread",
            metadata={"a": 1},
        )
        snapshot = handle.public_snapshot()
        assert snapshot["id"] == "test-id"
        assert "opaque_ref" not in snapshot

    def test_touch_updates_last_used_at(self):
        handle = KVHandle(backend="stub", model_id="test")
        original = handle.last_used_at
        handle.touch()
        assert handle.last_used_at >= original

    def test_opaque_ref_not_in_repr(self):
        handle = KVHandle(
            backend="mlx",
            model_id="test",
            opaque_ref="secret-data",
        )
        repr_str = repr(handle)
        assert "secret-data" not in repr_str

    def test_different_handles_have_unique_ids(self):
        h1 = KVHandle(backend="stub", model_id="m")
        h2 = KVHandle(backend="stub", model_id="m")
        assert h1.id != h2.id


class TestKVCapability:
    def test_enum_values(self):
        assert KVCapability.UNSUPPORTED.value == "unsupported"
        assert KVCapability.PREFILL_ONLY.value == "prefill_only"
        assert KVCapability.RESUMABLE.value == "resumable"
        assert KVCapability.CLONEABLE.value == "cloneable"
        assert KVCapability.SERIALIZABLE.value == "serializable"

    def test_enum_from_string(self):
        assert KVCapability("unsupported") == KVCapability.UNSUPPORTED
        assert KVCapability("serializable") == KVCapability.SERIALIZABLE

    def test_unsupported_is_default_lowest(self):
        caps = list(KVCapability)
        assert caps[0] == KVCapability.UNSUPPORTED

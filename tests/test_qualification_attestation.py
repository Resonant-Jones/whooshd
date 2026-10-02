"""Contract tests for target-scoped runtime qualification attestations."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from whooshd.adapters.mlx_vlm import MlxVlmAdapter, MlxVlmConfig
from whooshd.qualification_attestation import (
    ATTESTATION_SCHEMA_VERSION,
    CANONICALIZATION_PROFILE,
    AttestationIdentityError,
    build_attestation,
    canonicalize_identity_document,
    collect_mlx_vlm_target_material,
    digest_identity_document,
)


def _identity() -> dict[str, object]:
    """The Stage 2F normative vector in intentionally non-canonical order."""
    return {
        "tool_template_parser": {
            "relationship": "distinct",
            "identity_fingerprint": "sha256:" + "c" * 64,
        },
        "tokenizer": {
            "identity_fingerprint": "sha256:" + "b" * 64,
            "implementation": "GemmaTokenizer",
        },
        "structured_transport": {
            "protocol_version": "model-turn.strict-json-schema.v1",
            "mode": "strict_json_schema",
        },
        "structured_decoder": {"version": "1.7.6", "package": "llguidance"},
        "serving_runtime": {"version": "0.6.2", "package": "mlx-vlm"},
        "runtime_kind": "mlx_vlm",
        "resolved_model_id": "example.org/Gemma-4-12B-IT-QAT-4bit",
        "quantization": "qat-4bit",
        "qualification_protocol_version": "model-turn.strict-json-schema.v1",
        "whooshd_build_identity": "whooshd-0.1.0rc1+synthetic",
        "invocation_model_id": "Gemma-4-12B-IT-QAT-4bit",
        "chat_template_fingerprint": "sha256:" + "a" * 64,
        "canonicalization_profile": CANONICALIZATION_PROFILE,
        "attestation_schema_version": ATTESTATION_SCHEMA_VERSION,
        "artifact_identity": {
            "value": "sha256:" + "d" * 64,
            "kind": "manifest_fingerprint",
        },
        "adapter": {"semantic_build": "MlxVlmAdapter-Café-v1", "name": "mlx-vlm"},
    }


def test_stage_2f_normative_fixed_vector_is_exact():
    identity = _identity()

    canonical = canonicalize_identity_document(identity)

    assert b"MlxVlmAdapter-Caf\xc3\xa9-v1" in canonical
    assert digest_identity_document(identity) == (
        "sha256:5f1923d1afa0f3a804bd3c12f37486b9f6b692baf43a294c766610399d95725f"
    )


def test_object_order_and_nfc_normalization_are_stable():
    identity = _identity()
    reordered = dict(reversed(list(identity.items())))
    decomposed = copy.deepcopy(reordered)
    decomposed["adapter"]["semantic_build"] = "MlxVlmAdapter-Cafe\u0301-v1"  # type: ignore[index]

    assert canonicalize_identity_document(identity) == canonicalize_identity_document(reordered)
    assert canonicalize_identity_document(identity) == canonicalize_identity_document(decomposed)
    assert digest_identity_document(identity) == digest_identity_document(decomposed)


def test_case_and_whitespace_remain_material():
    identity = _identity()
    case_changed = copy.deepcopy(identity)
    spaced = copy.deepcopy(identity)
    case_changed["invocation_model_id"] = "gemma-4-12b-it-qat-4bit"
    spaced["whooshd_build_identity"] = " whooshd-0.1.0rc1+synthetic "

    assert digest_identity_document(identity) != digest_identity_document(case_changed)
    assert digest_identity_document(identity) != digest_identity_document(spaced)


def test_unordered_or_unknown_identity_input_is_rejected():
    unordered = _identity()
    unordered["quantization"] = {"qat-4bit"}
    with pytest.raises(AttestationIdentityError):
        canonicalize_identity_document(unordered)

    extended = _identity()
    extended["request_id"] = "request-7"
    with pytest.raises(AttestationIdentityError):
        canonicalize_identity_document(extended)


@pytest.mark.parametrize(
    "path",
    [
        ("artifact_identity", "value"),
        ("quantization",),
        ("serving_runtime", "version"),
        ("structured_decoder", "version"),
        ("tokenizer", "implementation"),
        ("chat_template_fingerprint",),
        ("structured_transport", "mode"),
    ],
)
def test_missing_required_material_never_gets_a_digest(path: tuple[str, ...]):
    identity = _identity()
    cursor: dict[str, object] = identity
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[assignment,index]
    cursor[path[-1]] = None

    attestation = build_attestation(identity)

    assert attestation.attestation_digest is None
    assert attestation.digest_algorithm is None
    assert "unknown" not in attestation.model_dump_json().casefold()


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("artifact_identity", "value"), "sha256:" + "e" * 64),
        (("quantization",), "bits-8"),
        (("serving_runtime", "version"), "0.6.3"),
        (("structured_decoder", "version"), "1.7.7"),
        (("tokenizer", "implementation"), "OtherTokenizer"),
        (("chat_template_fingerprint",), "sha256:" + "e" * 64),
        (("structured_transport", "protocol_version"), "model-turn.strict-json-schema.v2"),
    ],
)
def test_material_changes_change_digest(path: tuple[str, ...], replacement: str):
    identity = _identity()
    changed = copy.deepcopy(identity)
    cursor: dict[str, object] = changed
    for key in path[:-1]:
        cursor = cursor[key]  # type: ignore[assignment,index]
    cursor[path[-1]] = replacement

    assert digest_identity_document(identity) != digest_identity_document(changed)


def test_target_collection_fingerprints_private_files_without_serializing_them(tmp_path):
    target = tmp_path / "models--mlx-community--gemma-4-12B-it-qat-4bit"
    target.mkdir()
    (target / "config.json").write_text(
        json.dumps({"quantization": {"bits": 4, "group_size": 64, "mode": "affine"}}),
        encoding="utf-8",
    )
    (target / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "GemmaTokenizer"}), encoding="utf-8"
    )
    (target / "chat_template.jinja").write_text(
        "PRIVATE_TEMPLATE_SENTINEL {{ messages }}", encoding="utf-8"
    )

    material = collect_mlx_vlm_target_material(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id=str(target),
        model_source=str(target),
    )
    attestation = build_attestation(material)
    serialized = attestation.model_dump_json(exclude_none=True)

    assert material["resolved_model_id"] == "mlx-community/gemma-4-12B-it-qat-4bit"
    assert material["quantization"] == "bits-4-group-64-affine"
    assert "PRIVATE_TEMPLATE_SENTINEL" not in serialized
    assert str(target) not in serialized
    assert attestation.attestation_digest is None

    (target / "chat_template.jinja").write_text(
        "PRIVATE_TEMPLATE_SENTINEL changed", encoding="utf-8"
    )
    changed = collect_mlx_vlm_target_material(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id=str(target),
        model_source=str(target),
    )
    assert changed["chat_template_fingerprint"] != material["chat_template_fingerprint"]


def test_complete_pinned_target_fixture_can_be_retained_with_bounded_reference():
    attestation = build_attestation(_identity())
    reference = attestation.reference()

    assert attestation.complete is True
    assert attestation.attestation_digest is not None
    assert reference.attestation_digest == attestation.attestation_digest
    assert reference.canonicalization_profile == CANONICALIZATION_PROFILE
    assert "MlxVlmAdapter-Café-v1" not in reference.model_dump_json()


def test_adapter_retains_once_and_invalidates_on_actual_unload(monkeypatch):
    adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="/private/models/target"))
    calls = 0

    def collect(**kwargs):
        nonlocal calls
        calls += 1
        identity = _identity()
        identity["invocation_model_id"] = kwargs["invocation_model_id"]
        identity["resolved_model_id"] = "mlx-community/gemma-4-12B-it-qat-4bit"
        return identity

    monkeypatch.setattr("whooshd.adapters.mlx_vlm.collect_mlx_vlm_target_material", collect)
    monkeypatch.setattr(adapter, "_managed_package_measurement", lambda: (None, None))

    first = adapter.qualification_attestation_for_target(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id="/private/models/target",
    )
    second = adapter.qualification_attestation_for_target(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id="/private/models/target",
    )
    assert first.attestation_digest == second.attestation_digest
    assert calls == 1

    asyncio.run(adapter.unload())
    adapter.qualification_attestation_for_target(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id="/private/models/target",
    )
    assert calls == 2


def test_adapter_invalidates_when_upstream_reports_target_replacement(monkeypatch):
    adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="/private/models/target"))
    calls = 0

    def collect(**kwargs):
        nonlocal calls
        calls += 1
        identity = _identity()
        identity["invocation_model_id"] = kwargs["invocation_model_id"]
        identity["resolved_model_id"] = "mlx-community/gemma-4-12B-it-qat-4bit"
        return identity

    monkeypatch.setattr("whooshd.adapters.mlx_vlm.collect_mlx_vlm_target_material", collect)
    monkeypatch.setattr(adapter, "_managed_package_measurement", lambda: (None, None))
    adapter.qualification_attestation_for_target(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id="/private/models/target",
    )
    adapter._observe_upstream_model({"data": [{"id": "target-b"}]})
    adapter.qualification_attestation_for_target(
        invocation_model_id="gemma-4-12b-it-qat-4bit",
        resolved_model_id="/private/models/target",
    )

    assert calls == 2


def test_mlx_vlm_attestation_does_not_change_tool_capability():
    adapter = MlxVlmAdapter(MlxVlmConfig(enabled=True, model="test"))

    assert adapter.supports_tools is False

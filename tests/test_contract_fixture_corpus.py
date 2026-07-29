"""Invariant tests for the repository-neutral contract fixture corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from whooshd.runtime.threadwake.keys import canonicalize_content


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts"
    / "fixtures"
    / "v1"
    / "contract-fixtures.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_corpus_has_required_contract_surfaces():
    fixture = _load_fixture()

    assert fixture["fixture_version"] == 1
    assert {
        "contract",
        "request_correlation",
        "model_record",
        "errors",
        "stream_success",
        "stream_failure",
        "cancellation",
        "capability_mismatch",
        "threadwake_normalization",
    } <= fixture.keys()


def test_request_correlation_keeps_upstream_and_runtime_ids_distinct():
    correlation = _load_fixture()["request_correlation"]

    codexify_id = correlation["request_headers"]["X-Request-ID"]
    echoed_id = correlation["response_headers"]["X-Request-ID"]
    whoosh_id = correlation["response_headers"]["X-Whoosh-Request-ID"]

    assert echoed_id == codexify_id
    assert whoosh_id != codexify_id
    assert correlation["rules"]["codexify_request_id_is_preserved"] is True
    assert correlation["rules"]["whoosh_request_id_is_distinct"] is True


def test_model_record_is_public_alias_without_machine_path():
    record = _load_fixture()["model_record"]
    serialized = json.dumps(record, sort_keys=True)

    assert record["id"] == "fixture-text-model"
    assert record["state"] == "ready"
    assert record["runtime"] == "stub"
    assert record["model_revision"].startswith("sha256:")
    assert record["tokenizer_revision"].startswith("sha256:")
    assert record["template_revision"].startswith("sha256:")
    assert "/Users/" not in serialized
    assert "/Volumes/" not in serialized
    assert "file://" not in serialized


def test_error_fixture_has_unique_stable_conditions_and_codes():
    errors = _load_fixture()["errors"]
    conditions = [item["condition"] for item in errors]
    codes = [item["code"] for item in errors]

    assert len(conditions) == len(set(conditions))
    assert len(codes) == len(set(codes))
    assert {
        "invalid_request",
        "model_unknown",
        "cancelled",
        "capability_unsupported",
        "model_warming",
        "overloaded",
        "runtime_unavailable",
        "execution_timeout",
    } == set(codes)

    by_code = {item["code"]: item for item in errors}
    assert by_code["model_warming"]["http_status"] == 425
    assert by_code["overloaded"]["http_status"] == 429
    assert by_code["model_warming"]["retryable"] is True
    assert by_code["overloaded"]["retryable"] is True
    assert by_code["model_unknown"]["retryable"] is False


def test_success_stream_requires_explicit_terminal_success():
    success = _load_fixture()["stream_success"]
    wire = "\n\n".join(success["events"])

    assert success["persistable"] is True
    assert success["requires_finish_reason_stop"] is True
    assert success["requires_done"] is True
    assert '"finish_reason":"stop"' in wire
    assert wire.rstrip().endswith("data: [DONE]")


def test_failed_stream_is_noncanonical_and_never_emits_done():
    failure = _load_fixture()["stream_failure"]
    wire = "\n\n".join(failure["events"])

    assert failure["persistable"] is False
    assert failure["must_not_emit_done"] is True
    assert failure["partial_output_classification"] == "diagnostic_only"
    assert '"error"' in wire
    assert "[DONE]" not in wire


def test_capability_mismatch_fails_before_execution():
    mismatch = _load_fixture()["capability_mismatch"]

    assert mismatch["requested"] not in mismatch["model_modalities"]
    assert mismatch["http_status"] == 422
    assert mismatch["code"] == "capability_unsupported"
    assert mismatch["execution_must_not_begin"] is True


def test_threadwake_normalization_vector_recomputes_exact_digest():
    vector = _load_fixture()["threadwake_normalization"]
    parsed_canonical_utf8 = vector["canonical_utf8"]
    canonical_utf8 = canonicalize_content(vector["input"])
    expected_canonical_utf8 = '{"content":"Hello\\nworld","type":"text"}'
    digest = hashlib.sha256(canonical_utf8.encode("utf-8")).hexdigest()

    assert vector["algorithm"] == "whoosh.threadwake.text.v1"
    assert parsed_canonical_utf8 == '{"content":"Hello\nworld","type":"text"}'
    assert canonical_utf8 == expected_canonical_utf8
    assert parsed_canonical_utf8 != canonical_utf8
    parsed_digest = hashlib.sha256(parsed_canonical_utf8.encode("utf-8")).hexdigest()
    assert parsed_digest == vector["sha256"]
    assert digest == "1ebd886e692483d6e9e752506fdefb70b4e77b846f2d908f876a5632bb204ffb"


def test_fixture_contains_no_obvious_secret_or_private_path_sentinels():
    serialized = _FIXTURE_PATH.read_text(encoding="utf-8")

    forbidden = (
        "sk-",
        "api_key",
        "authorization",
        "bearer ",
        "/Users/",
        "/Volumes/",
        "BEGIN PRIVATE KEY",
    )
    lowered = serialized.lower()
    for value in forbidden:
        assert value.lower() not in lowered

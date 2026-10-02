"""Bounded, target-scoped runtime qualification attestations.

This module owns the immutable v1 identity document and its digest boundary.
It intentionally does not decide whether a target is qualified, advertise a
capability, or treat configuration as evidence of the process that served a
request.  Callers may retain partial observations, but only a complete v1
identity document receives a digest.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ATTESTATION_SCHEMA_VERSION = "whooshd.qualification-attestation.v1"
CANONICALIZATION_PROFILE = "whooshd.qualification-attestation.canonical-json.v1"
DIGEST_ALGORITHM = "sha256"
QUALIFICATION_PROTOCOL_VERSION = "model-turn.strict-json-schema.v1"

_PLACEHOLDERS = {"unknown", "unavailable", "n/a"}
_SAFE_FILE_MAX_BYTES = 2 * 1024 * 1024
_MODEL_CACHE_NAME = re.compile(r"^models--(?P<owner>[^/]+)--(?P<repo>[^/]+)$")
_PUBLIC_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?$")


class AttestationIdentityError(ValueError):
    """Raised when a v1 identity document cannot be canonically represented."""


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = Field(None, max_length=64)
    value: str | None = Field(None, max_length=256)


class AdapterIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, max_length=64)
    semantic_build: str | None = Field(None, max_length=128)


class PackageIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str | None = Field(None, max_length=64)
    version: str | None = Field(None, max_length=128)


class TokenizerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation: str | None = Field(None, max_length=128)
    identity_fingerprint: str | None = Field(None, max_length=128)


class ToolTemplateParserIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship: str | None = Field(None, max_length=64)
    identity_fingerprint: str | None = Field(None, max_length=128)


class StructuredTransportIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str | None = Field(None, max_length=128)
    protocol_version: str | None = Field(None, max_length=128)


class RuntimeQualificationAttestationReference(BaseModel):
    """Small content-free pointer carried by request provenance."""

    model_config = ConfigDict(extra="forbid")

    attestation_schema_version: str = Field(ATTESTATION_SCHEMA_VERSION, max_length=128)
    canonicalization_profile: str = Field(CANONICALIZATION_PROFILE, max_length=128)
    digest_algorithm: str | None = Field(None, max_length=32)
    attestation_digest: str | None = Field(None, max_length=80)
    invocation_model_id: str | None = Field(None, max_length=256)
    resolved_model_id: str | None = Field(None, max_length=256)
    runtime_kind: str | None = Field(None, max_length=64)
    adapter_name: str | None = Field(None, max_length=64)


class RuntimeQualificationAttestation(BaseModel):
    """Safe retained target identity, complete or explicitly incomplete."""

    model_config = ConfigDict(extra="forbid")

    attestation_schema_version: str = Field(ATTESTATION_SCHEMA_VERSION, max_length=128)
    canonicalization_profile: str = Field(CANONICALIZATION_PROFILE, max_length=128)
    invocation_model_id: str | None = Field(None, max_length=256)
    resolved_model_id: str | None = Field(None, max_length=256)
    artifact_identity: ArtifactIdentity = Field(default_factory=ArtifactIdentity)
    quantization: str | None = Field(None, max_length=128)
    runtime_kind: str | None = Field(None, max_length=64)
    adapter: AdapterIdentity = Field(default_factory=AdapterIdentity)
    whooshd_build_identity: str | None = Field(None, max_length=128)
    serving_runtime: PackageIdentity = Field(default_factory=PackageIdentity)
    structured_decoder: PackageIdentity = Field(default_factory=PackageIdentity)
    tokenizer: TokenizerIdentity = Field(default_factory=TokenizerIdentity)
    chat_template_fingerprint: str | None = Field(None, max_length=128)
    tool_template_parser: ToolTemplateParserIdentity = Field(
        default_factory=ToolTemplateParserIdentity
    )
    structured_transport: StructuredTransportIdentity = Field(
        default_factory=StructuredTransportIdentity
    )
    qualification_protocol_version: str | None = Field(None, max_length=128)
    digest_algorithm: str | None = Field(None, max_length=32)
    attestation_digest: str | None = Field(None, max_length=80)

    def material_identity(self) -> dict[str, object] | None:
        """Return the exact v1 digest document only when all evidence exists."""
        candidate: dict[str, object] = {
            "attestation_schema_version": self.attestation_schema_version,
            "canonicalization_profile": self.canonicalization_profile,
            "invocation_model_id": self.invocation_model_id,
            "resolved_model_id": self.resolved_model_id,
            "artifact_identity": self.artifact_identity.model_dump(),
            "quantization": self.quantization,
            "runtime_kind": self.runtime_kind,
            "adapter": self.adapter.model_dump(),
            "whooshd_build_identity": self.whooshd_build_identity,
            "serving_runtime": self.serving_runtime.model_dump(),
            "structured_decoder": self.structured_decoder.model_dump(),
            "tokenizer": self.tokenizer.model_dump(),
            "chat_template_fingerprint": self.chat_template_fingerprint,
            "tool_template_parser": self.tool_template_parser.model_dump(),
            "structured_transport": self.structured_transport.model_dump(),
            "qualification_protocol_version": self.qualification_protocol_version,
        }
        try:
            canonicalize_identity_document(candidate)
        except AttestationIdentityError:
            return None
        return candidate

    @property
    def complete(self) -> bool:
        return self.attestation_digest is not None

    def reference(self) -> RuntimeQualificationAttestationReference:
        return RuntimeQualificationAttestationReference(
            attestation_schema_version=self.attestation_schema_version,
            canonicalization_profile=self.canonicalization_profile,
            digest_algorithm=self.digest_algorithm,
            attestation_digest=self.attestation_digest,
            invocation_model_id=self.invocation_model_id,
            resolved_model_id=self.resolved_model_id,
            runtime_kind=self.runtime_kind,
            adapter_name=self.adapter.name,
        )


_V1_DOCUMENT_SPEC: dict[str, object] = {
    "attestation_schema_version": str,
    "canonicalization_profile": str,
    "invocation_model_id": str,
    "resolved_model_id": str,
    "artifact_identity": {"kind": str, "value": str},
    "quantization": str,
    "runtime_kind": str,
    "adapter": {"name": str, "semantic_build": str},
    "whooshd_build_identity": str,
    "serving_runtime": {"package": str, "version": str},
    "structured_decoder": {"package": str, "version": str},
    "tokenizer": {"implementation": str, "identity_fingerprint": str},
    "chat_template_fingerprint": str,
    "tool_template_parser": {"relationship": str, "identity_fingerprint": str},
    "structured_transport": {"mode": str, "protocol_version": str},
    "qualification_protocol_version": str,
}


def _valid_evidence(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.casefold() not in _PLACEHOLDERS
    )


def _normalize_object(value: object, spec: object, path: str) -> object:
    if isinstance(spec, type):
        if spec is str and _valid_evidence(value):
            return unicodedata.normalize("NFC", value)
        raise AttestationIdentityError(f"invalid required evidence at {path}")

    if not isinstance(spec, dict) or not isinstance(value, Mapping):
        raise AttestationIdentityError(f"invalid identity object at {path}")

    normalized: dict[str, object] = {}
    normalized_keys: set[str] = set()
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise AttestationIdentityError(f"non-string identity key at {path}")
        key = unicodedata.normalize("NFC", raw_key)
        if key in normalized_keys:
            raise AttestationIdentityError(f"duplicate normalized identity key at {path}")
        normalized_keys.add(key)
        if key not in spec:
            raise AttestationIdentityError(f"unknown identity field at {path}.{key}")
        normalized[key] = _normalize_object(raw_value, spec[key], f"{path}.{key}")

    if set(normalized) != set(spec):
        raise AttestationIdentityError(f"incomplete identity object at {path}")
    return normalized


def canonicalize_identity_document(identity: Mapping[str, object]) -> bytes:
    """Return immutable canonical-json.v1 bytes for a complete v1 identity."""
    normalized = _normalize_object(identity, _V1_DOCUMENT_SPEC, "identity")
    assert isinstance(normalized, dict)
    if normalized["attestation_schema_version"] != ATTESTATION_SCHEMA_VERSION:
        raise AttestationIdentityError("unexpected attestation schema version")
    if normalized["canonicalization_profile"] != CANONICALIZATION_PROFILE:
        raise AttestationIdentityError("unexpected canonicalization profile")
    try:
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AttestationIdentityError("identity is not JSON-compatible") from exc
    return serialized.encode("utf-8")


def digest_identity_document(identity: Mapping[str, object]) -> str:
    """Digest a complete v1 identity with the contract's serialized form."""
    return f"{DIGEST_ALGORITHM}:{hashlib.sha256(canonicalize_identity_document(identity)).hexdigest()}"


def fingerprint_text(value: str) -> str:
    """Return a bounded SHA-256 fingerprint without retaining source text."""
    return f"{DIGEST_ALGORITHM}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def build_attestation(material: Mapping[str, object]) -> RuntimeQualificationAttestation:
    """Build a safe attestation and digest it only if v1 evidence is complete."""
    data = dict(material)
    data.setdefault("attestation_schema_version", ATTESTATION_SCHEMA_VERSION)
    data.setdefault("canonicalization_profile", CANONICALIZATION_PROFILE)
    attestation = RuntimeQualificationAttestation(**data)
    identity = attestation.material_identity()
    if identity is None:
        return attestation
    return attestation.model_copy(
        update={
            "digest_algorithm": DIGEST_ALGORITHM,
            "attestation_digest": digest_identity_document(identity),
        }
    )


def _read_small_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > _SAFE_FILE_MAX_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_small_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _SAFE_FILE_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _manifest_fingerprint(root: Path) -> str | None:
    parts: list[str] = []
    for filename in (
        "config.json",
        "tokenizer_config.json",
        "generation_config.json",
        "model.safetensors.index.json",
        "chat_template.jinja",
    ):
        contents = _read_small_text(root / filename)
        if contents is not None:
            parts.append(f"{filename}\0{fingerprint_text(contents)}")
    return fingerprint_text("\n".join(parts)) if parts else None


def _public_model_id(
    model_path: Path | None,
    resolved_model_id: str | None,
) -> str | None:
    if resolved_model_id and _PUBLIC_MODEL_ID.fullmatch(resolved_model_id):
        return resolved_model_id
    if model_path is None:
        return None
    match = _MODEL_CACHE_NAME.match(model_path.name)
    if match:
        return f"{match.group('owner')}/{match.group('repo')}"
    return None


def _public_invocation_model_id(value: str) -> str | None:
    return value if _PUBLIC_MODEL_ID.fullmatch(value) else None


def _quantization_from_config(config: Mapping[str, Any]) -> str | None:
    quantization = config.get("quantization")
    if not isinstance(quantization, Mapping):
        return None
    bits = quantization.get("bits")
    if not isinstance(bits, int) or isinstance(bits, bool) or bits <= 0:
        return None
    group_size = quantization.get("group_size")
    mode = quantization.get("mode") or quantization.get("quantization_type")
    components = [f"bits-{bits}"]
    if isinstance(group_size, int) and not isinstance(group_size, bool) and group_size > 0:
        components.append(f"group-{group_size}")
    if isinstance(mode, str) and mode:
        components.append(unicodedata.normalize("NFC", mode))
    return "-".join(components)


def collect_mlx_vlm_target_material(
    *,
    invocation_model_id: str,
    resolved_model_id: str | None,
    model_source: str | None,
    serving_runtime: Mapping[str, str] | None = None,
    structured_decoder: Mapping[str, str] | None = None,
    structured_transport: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Collect bounded material observed by the MLX-VLM target owner.

    The serving package, decoder, and transport fields are deliberately absent
    unless the owner of the serving process supplied measured values.  A local
    model directory alone must not make an external sidecar appear qualified.
    """
    from whooshd import __version__

    model_path = Path(model_source) if model_source and Path(model_source).is_dir() else None
    model_config = _read_small_json(model_path / "config.json") if model_path else {}
    tokenizer_config = _read_small_json(model_path / "tokenizer_config.json") if model_path else {}
    model_config = model_config or {}
    tokenizer_config = tokenizer_config or {}

    template_text = _read_small_text(model_path / "chat_template.jinja") if model_path else None
    if template_text is None:
        candidate_template = tokenizer_config.get("chat_template")
        template_text = candidate_template if isinstance(candidate_template, str) else None

    tokenizer_class = tokenizer_config.get("tokenizer_class")
    tokenizer_identity = fingerprint_text(json.dumps(
        tokenizer_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )) if tokenizer_config else None
    manifest = _manifest_fingerprint(model_path) if model_path else None

    material: dict[str, object] = {
        "invocation_model_id": _public_invocation_model_id(invocation_model_id),
        "resolved_model_id": _public_model_id(model_path, resolved_model_id),
        "artifact_identity": {
            "kind": "manifest_fingerprint" if manifest else None,
            "value": manifest,
        },
        "quantization": _quantization_from_config(model_config),
        "runtime_kind": "mlx_vlm",
        "adapter": {
            "name": "mlx-vlm",
            "semantic_build": f"MlxVlmAdapter-{__version__}",
        },
        "whooshd_build_identity": f"whooshd-{__version__}",
        "serving_runtime": dict(serving_runtime or {}),
        "structured_decoder": dict(structured_decoder or {}),
        "tokenizer": {
            "implementation": tokenizer_class if isinstance(tokenizer_class, str) else None,
            "identity_fingerprint": tokenizer_identity,
        },
        "chat_template_fingerprint": fingerprint_text(template_text) if template_text else None,
        "tool_template_parser": {},
        "structured_transport": dict(structured_transport or {}),
        "qualification_protocol_version": (
            QUALIFICATION_PROTOCOL_VERSION if structured_transport else None
        ),
    }
    return material

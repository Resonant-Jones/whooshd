"""Whoosh'd Model Registry — persistent model-store management.

This package provides the filesystem-backed model-store layer:
  - bootstrap_model_store(): creates the directory layout and manifest
  - inspect_model_candidate(): classify a model artifact path
  - write_candidate_record(): persist candidate metadata
  - register_model_candidate(): promote a candidate into a registered model
  - validate_registered_model_compatibility(): map registered models to adapters
  - contract types for store layout, manifest, candidates, registration, compatibility

Separate from the runtime model registry (``whooshd/registry.py``) which
describes *configured* models for inference routing.
"""

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import (
    inspect_model_candidate,
    write_candidate_record,
)
from whooshd.model_registry.compatibility import (
    validate_registered_model_compatibility,
)
from whooshd.model_registry.contracts import (
    ModelCandidate,
    ModelCandidateFormat,
    ModelCandidateInspectionResult,
    ModelCandidateStatus,
    ModelRegistrationResult,
    ModelRegistryManifest,
    ModelRegistryState,
    ModelStoreLayout,
    RegisteredModel,
    RegisteredModelAdapterKind,
    RegisteredModelCompatibilityResult,
    RegisteredModelCompatibilityStatus,
    RegisteredModelStatus,
    RegisteredModelStorageMode,
)
from whooshd.model_registry.registration import register_model_candidate

__all__ = [
    "bootstrap_model_store",
    "inspect_model_candidate",
    "register_model_candidate",
    "validate_registered_model_compatibility",
    "write_candidate_record",
    "ModelCandidate",
    "ModelCandidateFormat",
    "ModelCandidateInspectionResult",
    "ModelCandidateStatus",
    "ModelRegistrationResult",
    "ModelRegistryManifest",
    "ModelRegistryState",
    "ModelStoreLayout",
    "RegisteredModel",
    "RegisteredModelAdapterKind",
    "RegisteredModelCompatibilityResult",
    "RegisteredModelCompatibilityStatus",
    "RegisteredModelStatus",
    "RegisteredModelStorageMode",
]

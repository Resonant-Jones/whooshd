"""Whoosh'd Model Registry — persistent model-store management.

This package provides the filesystem-backed model-store layer:
  - bootstrap_model_store(): creates the directory layout and manifest
  - inspect_model_candidate(): classify a model artifact path
  - write_candidate_record(): persist candidate metadata
  - contract types: ModelStoreLayout, ModelRegistryManifest, ModelRegistryState,
    ModelCandidate, ModelCandidateInspectionResult

Separate from the runtime model registry (``whooshd/registry.py``) which
describes *configured* models for inference routing.
"""

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.candidates import (
    inspect_model_candidate,
    write_candidate_record,
)
from whooshd.model_registry.contracts import (
    ModelCandidate,
    ModelCandidateFormat,
    ModelCandidateInspectionResult,
    ModelCandidateStatus,
    ModelRegistryManifest,
    ModelRegistryState,
    ModelStoreLayout,
)

__all__ = [
    "bootstrap_model_store",
    "inspect_model_candidate",
    "write_candidate_record",
    "ModelCandidate",
    "ModelCandidateFormat",
    "ModelCandidateInspectionResult",
    "ModelCandidateStatus",
    "ModelRegistryManifest",
    "ModelRegistryState",
    "ModelStoreLayout",
]

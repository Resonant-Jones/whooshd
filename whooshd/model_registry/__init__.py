"""Whoosh'd Model Registry — persistent model-store management.

This package provides the filesystem-backed model-store layer:
  - bootstrap_model_store(): creates the directory layout and manifest
  - contract types: ModelStoreLayout, ModelRegistryManifest, ModelRegistryState

Separate from the runtime model registry (``whooshd/registry.py``) which
describes *configured* models for inference routing.
"""

from whooshd.model_registry.bootstrap import bootstrap_model_store
from whooshd.model_registry.contracts import (
    ModelRegistryManifest,
    ModelRegistryState,
    ModelStoreLayout,
)

__all__ = [
    "bootstrap_model_store",
    "ModelRegistryManifest",
    "ModelRegistryState",
    "ModelStoreLayout",
]

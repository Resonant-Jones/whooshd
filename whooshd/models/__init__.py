"""Model resolution — filesystem path resolution primitives.

These modules are standalone.  They do not depend on runtime adapters,
API handlers, Hugging Face libraries, or Codexify integration.
"""

from whooshd.models.resolver import resolve_model
from whooshd.models.types import (
    ModelFormat,
    ModelResolutionRequest,
    ModelResolutionResult,
    ResolutionStatus,
)

__all__ = [
    "resolve_model",
    "ModelFormat",
    "ModelResolutionRequest",
    "ModelResolutionResult",
    "ResolutionStatus",
]

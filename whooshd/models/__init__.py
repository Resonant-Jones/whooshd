"""Model resolution — filesystem path resolution primitives.

These modules are standalone.  They do not depend on runtime adapters,
API handlers, Hugging Face libraries, or Codexify integration.
"""

from whooshd.models.inventory import (
    list_external_model_inventory,
    parse_external_model_public_id,
    resolve_external_runtime_model,
)
from whooshd.models.resolver import resolve_model
from whooshd.models.routes import (
    ExternalRouteStatus,
    ExternalWeightRoute,
    ExternalWeightRouteStatus,
    get_available_route_paths,
    load_external_weight_routes,
    resolve_model_from_routes,
    validate_all_routes,
    validate_route_status,
)
from whooshd.models.types import (
    ExternalModelInventoryEntry,
    ExternalRuntimeResolution,
    ModelFormat,
    ModelResolutionRequest,
    ModelResolutionResult,
    ResolutionStatus,
)

__all__ = [
    "resolve_model",
    "resolve_model_from_routes",
    "list_external_model_inventory",
    "parse_external_model_public_id",
    "resolve_external_runtime_model",
    "load_external_weight_routes",
    "validate_route_status",
    "validate_all_routes",
    "get_available_route_paths",
    "ExternalWeightRoute",
    "ExternalWeightRouteStatus",
    "ExternalRouteStatus",
    "ExternalModelInventoryEntry",
    "ExternalRuntimeResolution",
    "ModelFormat",
    "ModelResolutionRequest",
    "ModelResolutionResult",
    "ResolutionStatus",
]

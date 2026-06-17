"""Backend materialization interface for ThreadWake Phase M18.

Defines the runtime contract between ThreadWake and inference backends
for snapshot materialization.  No real KV snapshots are created.
No snapshots are restored.  No KV state is reused.

All backends default to UNSUPPORTED.  MLX DECLAREs the interface.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


# ── Enums ──────────────────────────────────────────────────────────────────


class MaterializationCapability(str, Enum):
    UNSUPPORTED = "unsupported"
    DECLARED = "declared"
    EXPERIMENTAL = "experimental"
    SUPPORTED = "supported"


class MaterializationReason(str, Enum):
    BACKEND_NOT_REGISTERED = "backend_not_registered"
    BACKEND_DOES_NOT_SUPPORT_MATERIALIZATION = "backend_does_not_support_materialization"
    BACKEND_INTERFACE_DECLARED = "backend_interface_declared"
    BACKEND_EXPERIMENTAL = "backend_experimental"
    BACKEND_SUPPORTED = "backend_supported"
    MATERIALIZATION_DISABLED = "materialization_disabled"
    UNKNOWN_BACKEND = "unknown_backend"


# ── Result ─────────────────────────────────────────────────────────────────


@dataclass
class MaterializationCapabilityResult:
    backend: str = ""
    capability: str = MaterializationCapability.UNSUPPORTED.value
    reason: str = MaterializationReason.UNKNOWN_BACKEND.value
    supports_materialization: bool = False
    supports_restore: bool = False
    supports_reuse: bool = False
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def safe_dict(self) -> dict:
        return {
            "backend": self.backend, "capability": self.capability,
            "reason": self.reason, "supports_materialization": self.supports_materialization,
            "supports_restore": self.supports_restore, "supports_reuse": self.supports_reuse,
            "checked_at": self.checked_at,
        }


# ── Protocol ───────────────────────────────────────────────────────────────


class SnapshotMaterializer(Protocol):
    backend_name: str

    def capability(self) -> MaterializationCapabilityResult: ...
    def can_materialize(self) -> bool: ...
    def materialize(self, artifact: Any, manifest: Any) -> Any: ...


# ── No-op ──────────────────────────────────────────────────────────────────


class NoOpSnapshotMaterializer:
    backend_name = "noop"

    def capability(self) -> MaterializationCapabilityResult:
        return MaterializationCapabilityResult(
            backend=self.backend_name,
            capability=MaterializationCapability.UNSUPPORTED.value,
            reason=MaterializationReason.BACKEND_DOES_NOT_SUPPORT_MATERIALIZATION.value,
        )

    def can_materialize(self) -> bool:
        return False

    def materialize(self, artifact: Any, manifest: Any) -> Any:
        raise NotImplementedError("NoOpSnapshotMaterializer does not support materialization")


# ── MLX stub ───────────────────────────────────────────────────────────────


class MLXSnapshotMaterializer:
    backend_name = "mlx"

    def capability(self) -> MaterializationCapabilityResult:
        return MaterializationCapabilityResult(
            backend=self.backend_name,
            capability=MaterializationCapability.DECLARED.value,
            reason=MaterializationReason.BACKEND_INTERFACE_DECLARED.value,
        )

    def can_materialize(self) -> bool:
        return False

    def materialize(self, artifact: Any, manifest: Any) -> Any:
        raise NotImplementedError("MLXSnapshotMaterializer: snapshot materialization not yet implemented")


# ── Registry ───────────────────────────────────────────────────────────────


class MaterializerRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._materializers: dict[str, SnapshotMaterializer] = {}

    def register(self, materializer: SnapshotMaterializer) -> None:
        with self._lock:
            self._materializers[materializer.backend_name] = materializer

    def unregister(self, backend: str) -> None:
        with self._lock:
            self._materializers.pop(backend, None)

    def get(self, backend: str) -> SnapshotMaterializer:
        with self._lock:
            return self._materializers.get(backend, NoOpSnapshotMaterializer())

    def list_materializers(self) -> list[str]:
        with self._lock:
            return sorted(self._materializers.keys())

    def capability_summary(self) -> list[dict]:
        with self._lock:
            return [m.capability().safe_dict() for m in self._materializers.values()]

    def stats(self) -> dict:
        with self._lock:
            items = list(self._materializers.values())
        declared = sum(1 for m in items if m.capability().capability == MaterializationCapability.DECLARED.value)
        return {"registered_backends": len(items), "declared": declared, "experimental": 0, "supported": 0}

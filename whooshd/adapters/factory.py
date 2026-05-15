"""Adapter factory.

The HTTP layer calls ``create_adapter()`` once at startup (or lazily).
It never knows which concrete adapter is behind the protocol.
"""

from __future__ import annotations

from whooshd.adapters.base import InferenceAdapter
from whooshd.config import get_adapter_backend


def create_adapter() -> InferenceAdapter:
    """Return the configured inference adapter.

    Selection is controlled by the ``WHOOSHD_ADAPTER`` environment variable:
      * ``"stub"`` (default) — deterministic stub for tests
      * ``"mlx"`` — real mlx-lm inference
    """
    backend = get_adapter_backend()

    if backend == "mlx":
        from whooshd.adapters.mlx import MLXInferenceAdapter

        return MLXInferenceAdapter()

    # Default / unknown → stub
    from whooshd.adapters.stub import StubInferenceAdapter

    return StubInferenceAdapter()

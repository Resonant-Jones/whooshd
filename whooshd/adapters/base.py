"""Inference adapter protocol.

All inference backends (stub, mlx-lm, etc.) implement this interface
so the HTTP layer stays thin and testable.
"""

from __future__ import annotations

from typing import Protocol

from whooshd.contracts import GenerateRequest, GenerateResponse


class InferenceAdapter(Protocol):
    """Protocol for inference backends.

    Each adapter is responsible for accepting a GenerateRequest,
    running inference (or producing stub output), and returning
    a GenerateResponse.
    """

    @property
    def name(self) -> str:
        """Human-readable adapter identifier (e.g. 'stub', 'mlx-lm')."""
        ...

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run inference for a single generation request."""
        ...

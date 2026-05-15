"""Inference adapter package.

Adapters encapsulate model inference backends (stub, mlx-lm, etc.)
behind a common protocol so Whoosh'd routes can swap implementations
without changing request/response contracts.
"""

from whooshd.adapters.base import InferenceAdapter
from whooshd.adapters.stub import StubInferenceAdapter

__all__ = ["InferenceAdapter", "StubInferenceAdapter"]

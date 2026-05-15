"""Codexify compatibility probes.

These utilities verify that Whoosh'd behaves like a valid local provider
from Codexify's perspective — no knowledge of MLX internals required.
"""

from whooshd.compat.codexify_probe import CodexifyProbe

__all__ = ["CodexifyProbe"]

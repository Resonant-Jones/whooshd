"""Codexify compatibility probes.

These utilities verify that Whoosh'd behaves like a valid local provider
from Codexify's perspective — no knowledge of MLX internals required.

Imports are deferred so the package can be loaded without httpx installed.
"""


def __getattr__(name: str):
    if name == "CodexifyProbe":
        from whooshd.compat.codexify_probe import CodexifyProbe

        return CodexifyProbe
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

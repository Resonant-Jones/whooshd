"""Whoosh'd benchmark harness — measures throughput, not performance.

Measures a running Whoosh'd server from the outside using HTTP.
No tuning.  No cleverness.  Just measurement.
"""

from whooshd.bench.runner import run_benchmark

__all__ = ["run_benchmark"]

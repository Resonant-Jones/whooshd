"""Regression tests for adapter-declared concurrency ceilings.

Every *guarded* adapter enforces its own concurrency with a semaphore. If
the adapter does not also **declare** that ceiling, admission cannot
respect it: the broker's global limit may be higher than the lane's real
capacity, so the broker keeps admitting work that then stalls at the
adapter's semaphore and is eventually refused.

``RoutingTable`` reads the ceiling from the adapter
(``getattr(adapter, "max_concurrent_requests", None)``), so the declaration
is the whole mechanism.  These tests assert both halves: the adapter
declares the limit its semaphore enforces, and the router carries it
through to the resolution.
"""

from __future__ import annotations

import asyncio

import pytest

from whooshd.adapters.llama_cpp import LlamaCppAdapter
from whooshd.adapters.mlx_lm_server import MlxLmServerAdapter
from whooshd.adapters.mlx_vlm import MlxVlmAdapter
from whooshd.adapters.stub import StubInferenceAdapter


class TestGuardedAdaptersDeclareTheirCeiling:
    def test_mlx_lm_server_declares_its_ceiling(self):
        adapter = MlxLmServerAdapter.__new__(MlxLmServerAdapter)
        adapter._max_concurrent = 3
        assert adapter.max_concurrent_requests == 3

    def test_llama_cpp_declares_its_ceiling(self):
        adapter = LlamaCppAdapter.__new__(LlamaCppAdapter)
        adapter._max_concurrent = 2
        assert adapter.max_concurrent_requests == 2

    def test_mlx_vlm_declares_its_ceiling(self):
        adapter = MlxVlmAdapter.__new__(MlxVlmAdapter)
        adapter._max_concurrent = 2
        assert adapter.max_concurrent_requests == 2

    def test_the_stub_declares_no_ceiling(self):
        # The stub has no real backend, so it must not constrain admission.
        # The router reads the ceiling defensively, so an adapter that
        # simply does not declare one is treated as unconstrained.
        stub = StubInferenceAdapter()
        assert getattr(stub, "max_concurrent_requests", None) is None



class TestRoutingCarriesTheDeclaredCeiling:
    def test_resolution_reports_the_adapter_ceiling(self):
        from whooshd.routing import RuntimeRouter

        class _Adapter:
            kind = "lane_x"
            name = "lane_x"

            @property
            def max_concurrent_requests(self):
                return 2

            def is_loaded(self):
                return True

            def model_id(self):
                return "lane_x-model"

        router = RuntimeRouter()
        router.register(_Adapter())
        resolution = asyncio.run(router.resolve_model_runtime("lane_x-model"))
        assert resolution.lane_max_concurrent == 2
        assert resolution.lane == "lane_x"

    def test_an_adapter_without_a_ceiling_reports_none(self):
        from whooshd.routing import RuntimeRouter

        class _Adapter:
            kind = "lane_y"
            name = "lane_y"

            @property
            def max_concurrent_requests(self):
                return None

            def is_loaded(self):
                return True

            def model_id(self):
                return "lane_y-model"

        router = RuntimeRouter()
        router.register(_Adapter())
        resolution = asyncio.run(router.resolve_model_runtime("lane_y-model"))
        assert resolution.lane_max_concurrent is None

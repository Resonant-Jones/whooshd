"""Tests for the adapter factory."""

from __future__ import annotations

import os

import pytest

from whooshd.adapters.factory import create_adapter


class TestAdapterFactory:
    def test_default_is_stub(self, monkeypatch):
        monkeypatch.delenv("WHOOSHD_ADAPTER", raising=False)
        adapter = create_adapter()
        assert adapter.name == "stub"

    def test_explicit_stub(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ADAPTER", "stub")
        adapter = create_adapter()
        assert adapter.name == "stub"

    def test_unknown_backend_falls_back_to_stub(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ADAPTER", "garbage")
        adapter = create_adapter()
        assert adapter.name == "stub"

    def test_mlx_selected_when_configured(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_ADAPTER", "mlx")
        # Inject a mock mlx_lm into sys.modules before the adapter
        # tries to lazy-import it.
        import sys
        from unittest.mock import MagicMock

        mock_mlx = MagicMock()
        mock_mlx.load.return_value = (MagicMock(), MagicMock())
        sys.modules["mlx_lm"] = mock_mlx
        try:
            adapter = create_adapter()
            assert adapter.name == "mlx-lm"
        finally:
            del sys.modules["mlx_lm"]

    def test_factory_returns_protocol_compatible_object(self, monkeypatch):
        """Every adapter must expose name and supports_streaming."""
        for backend in ("stub",):
            monkeypatch.setenv("WHOOSHD_ADAPTER", backend)
            adapter = create_adapter()
            assert hasattr(adapter, "name")
            assert hasattr(adapter, "supports_streaming")
            assert hasattr(adapter, "chat_completion")
            assert hasattr(adapter, "generate")

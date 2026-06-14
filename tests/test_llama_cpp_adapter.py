"""Tests for the llama.cpp adapter skeleton.

Covers: factory selection, config construction, health probing (both modes),
inference stubs, request normalization placeholder, and registry metadata.
No llama.cpp binary or server is required.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whooshd.adapters.factory import create_adapter
from whooshd.adapters.llama_cpp import (
    LlamaCppAdapter,
    LlamaCppAdapterConfig,
    _LlamaCppNotImplementedError,
    _build_config_from_env,
    normalize_chat_request_for_llama_cpp,
)
from whooshd.contracts import (
    ChatCompletionRequest,
    ChatMessage,
    GenerateRequest,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_chat_request(model: str = "test-gguf") -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="Hello")],
    )


def _make_generate_request(prompt: str = "Hello") -> GenerateRequest:
    return GenerateRequest(prompt=prompt)


# ── Adapter identity ────────────────────────────────────────────────────────


class TestLlamaCppAdapterIdentity:
    def test_name_is_llama_cpp(self):
        adapter = LlamaCppAdapter()
        assert adapter.name == "llama-cpp"

    def test_supports_streaming_is_true(self):
        adapter = LlamaCppAdapter()
        assert adapter.supports_streaming is True

    def test_is_loaded_false_initially(self):
        adapter = LlamaCppAdapter()
        assert adapter.is_loaded() is False

    def test_model_id_returns_configured_path(self):
        config = LlamaCppAdapterConfig(model_path="/models/test.gguf")
        adapter = LlamaCppAdapter(config=config)
        assert adapter.model_id() == "/models/test.gguf"

    def test_model_id_none_when_not_configured(self):
        adapter = LlamaCppAdapter()
        assert adapter.model_id() is None


# ── Configuration ────────────────────────────────────────────────────────────


class TestLlamaCppAdapterConfig:
    def test_default_config(self):
        config = LlamaCppAdapterConfig()
        assert config.server_url is None
        assert config.binary_path is None
        assert config.model_path is None
        assert config.host == "127.0.0.1"
        assert config.port == 8080
        assert config.auto_start is False
        assert config.startup_timeout_seconds == 30.0
        assert config.health_timeout_seconds == 2.0
        assert config.extra_args == []

    def test_full_config(self):
        config = LlamaCppAdapterConfig(
            server_url="http://127.0.0.1:8080",
            binary_path="/usr/local/bin/llama-server",
            model_path="/models/test.gguf",
            host="0.0.0.0",
            port=9090,
            context_window=32768,
            parallel_slots=4,
            extra_args=["--mlock", "--no-mmap"],
            startup_timeout_seconds=60.0,
            health_timeout_seconds=5.0,
            auto_start=True,
        )
        assert config.server_url == "http://127.0.0.1:8080"
        assert config.binary_path == "/usr/local/bin/llama-server"
        assert config.model_path == "/models/test.gguf"
        assert config.host == "0.0.0.0"
        assert config.port == 9090
        assert config.context_window == 32768
        assert config.parallel_slots == 4
        assert config.extra_args == ["--mlock", "--no-mmap"]
        assert config.startup_timeout_seconds == 60.0
        assert config.health_timeout_seconds == 5.0
        assert config.auto_start is True

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_SERVER_URL", "http://10.0.0.1:8081")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_BINARY_PATH", "/opt/llama-server")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_HOST", "0.0.0.0")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_PORT", "9090")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_AUTO_START", "true")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_STARTUP_TIMEOUT_SECONDS", "45.0")
        monkeypatch.setenv("WHOOSHD_LLAMA_CPP_HEALTH_TIMEOUT_SECONDS", "3.5")

        config = _build_config_from_env()
        assert config.server_url == "http://10.0.0.1:8081"
        assert config.binary_path == "/opt/llama-server"
        assert config.host == "0.0.0.0"
        assert config.port == 9090
        assert config.auto_start is True
        assert config.startup_timeout_seconds == 45.0
        assert config.health_timeout_seconds == 3.5

    def test_config_server_url_strips_trailing_slash(self):
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080/")
        )
        assert adapter._server_url == "http://127.0.0.1:8080"

    def test_validate_server_url_rejects_bad_url(self):
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="not-a-valid-url")
        )
        with pytest.raises(ValueError, match="Invalid llama.cpp server_url"):
            adapter._validate_server_url()

    def test_validate_server_url_accepts_good_url(self):
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )
        adapter._validate_server_url()  # Should not raise


# ── Health probing (config-only mode — no server) ───────────────────────────


class TestHealthNoServer:
    async def test_health_no_server_url_returns_offline(self):
        """No server_url → clear offline status with model_warming lifecycle."""
        adapter = LlamaCppAdapter()
        status = await adapter.check_health()

        assert status.reachable is False
        assert status.model_lifecycle == "unloaded"
        assert "No server_url configured" in status.detail

    async def test_health_no_server_url_explicit_none(self):
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url=None)
        )
        status = await adapter.check_health()
        assert status.reachable is False
        assert "No server_url configured" in status.detail


# ── Health probing (mocked server) ──────────────────────────────────────────


class TestHealthMockedServer:
    async def test_health_reachable_via_health_endpoint(self):
        """Mock a reachable server returning 200 on /health."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(
                server_url="http://127.0.0.1:8080",
                health_timeout_seconds=2.0,
            )
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is True
            assert status.runner_status == "ready"
            assert status.model_lifecycle == "ready"
            assert status.raw_status == 200

    async def test_health_falls_back_to_v1_models(self):
        """If /health returns non-200, try /v1/models."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        health_response = MagicMock()
        health_response.status_code = 503

        models_response = MagicMock()
        models_response.status_code = 200

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=[health_response, models_response])
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is True
            assert status.model_lifecycle == "ready"

    async def test_health_v1_models_non_200_reports_warmup(self):
        """Server reachable but /v1/models returns non-200 → warming."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        health_response = MagicMock()
        health_response.status_code = 503

        models_response = MagicMock()
        models_response.status_code = 503

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=[health_response, models_response])
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is True
            assert status.model_lifecycle == "warmup"

    async def test_health_connection_refused(self):
        """Connection refused → offline."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        # Need to import the real exception class for patching
        import httpx

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is False
            assert "Connection refused" in status.detail
            assert status.model_lifecycle == "unloaded"

    async def test_health_timeout(self):
        """Health probe timeout → offline with timeout detail."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        import httpx

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is False
            assert "timed out" in status.detail.lower()

    async def test_health_unexpected_error(self):
        """Unexpected exception → degraded/failed."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        with patch("whooshd.adapters.llama_cpp.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=RuntimeError("boom"))
            mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

            status = await adapter.check_health()

            assert status.reachable is False
            assert status.runner_status == "degraded"
            assert status.model_lifecycle == "failed"
            assert "boom" in status.detail


# ── Lifecycle ────────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_warmup_no_server_is_noop(self):
        """warmup() is a no-op when no server_url is configured."""
        adapter = LlamaCppAdapter()
        await adapter.warmup()
        assert adapter.is_loaded() is False

    async def test_warmup_with_server_url_probes_health(self):
        """warmup() probes health when server_url is configured."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        with patch.object(adapter, "check_health", AsyncMock()) as mock_probe:
            await adapter.warmup()
            mock_probe.assert_called_once()

    async def test_warmup_with_server_survives_probe_failure(self):
        """warmup() should not raise if health probe fails."""
        adapter = LlamaCppAdapter(
            config=LlamaCppAdapterConfig(server_url="http://127.0.0.1:8080")
        )

        with patch.object(
            adapter, "check_health", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            # Should not raise.
            await adapter.warmup()

    async def test_unload_is_noop(self):
        """unload() is a no-op for the skeleton."""
        adapter = LlamaCppAdapter()
        await adapter.unload()  # Should not raise


# ── Inference stubs ──────────────────────────────────────────────────────────


class TestInferenceForwarding:
    """Tests for the inference forwarding methods (replacing old stubs)."""

    def test_generate_raises_runtime_unavailable_when_no_server(self):
        """Without a configured server URL, generate raises RuntimeUnavailable."""
        from whooshd.http_forwarding import RuntimeUnavailable

        adapter = LlamaCppAdapter()
        with pytest.raises(RuntimeUnavailable, match="not configured"):
            import asyncio
            asyncio.run(adapter.generate(_make_generate_request()))

    def test_chat_completion_raises_runtime_unavailable_when_no_server(self):
        """Without a configured server URL, chat_completion raises RuntimeUnavailable."""
        from whooshd.http_forwarding import RuntimeUnavailable

        adapter = LlamaCppAdapter()
        with pytest.raises(RuntimeUnavailable, match="not configured"):
            import asyncio
            asyncio.run(adapter.chat_completion(_make_chat_request()))

    def test_chat_completion_stream_raises_runtime_unavailable_when_no_server(self):
        """Without a configured server URL, chat_completion_stream raises RuntimeUnavailable."""
        from whooshd.http_forwarding import RuntimeUnavailable

        adapter = LlamaCppAdapter()
        with pytest.raises(RuntimeUnavailable, match="not configured"):
            import asyncio

            async def _run():
                async for _ in adapter.chat_completion_stream(_make_chat_request()):
                    pass

            asyncio.run(_run())

    def test_error_message_is_clear_and_references_server(self):
        """Error message must reference llama.cpp server configuration."""
        from whooshd.http_forwarding import RuntimeUnavailable

        adapter = LlamaCppAdapter()
        with pytest.raises(RuntimeUnavailable) as exc_info:
            import asyncio
            asyncio.run(adapter.chat_completion(_make_chat_request()))
        error_text = str(exc_info.value)
        assert "llama" in error_text.lower()
        assert "server" in error_text.lower()
        assert "not configured" in error_text.lower() or "not reachable" in error_text.lower()


# ── Request normalization placeholder ───────────────────────────────────────


class TestNormalizeChatRequest:
    def test_basic_normalization(self):
        req = ChatCompletionRequest(
            model="my-gguf-model",
            messages=[ChatMessage(role="user", content="Hello, world!")],
            stream=False,
        )
        result = normalize_chat_request_for_llama_cpp(req)
        assert result["model"] == "my-gguf-model"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Hello, world!"
        assert result["stream"] is False

    def test_multi_message_conversation(self):
        req = ChatCompletionRequest(
            model="gguf-model",
            messages=[
                ChatMessage(role="system", content="Be helpful."),
                ChatMessage(role="user", content="Hi"),
                ChatMessage(role="assistant", content="Hello!"),
                ChatMessage(role="user", content="What's up?"),
            ],
        )
        result = normalize_chat_request_for_llama_cpp(req)
        assert len(result["messages"]) == 4
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][3]["content"] == "What's up?"

    def test_does_not_raise_on_empty_messages(self):
        """Normalize should not validate — validation is Pydantic's job."""
        # Note: ChatCompletionRequest can't be constructed with empty messages
        # due to Pydantic validation, so we test the placeholder shape only.
        req = ChatCompletionRequest(
            model="m",
            messages=[ChatMessage(role="user", content="x")],
        )
        result = normalize_chat_request_for_llama_cpp(req)
        assert isinstance(result, dict)
        assert "model" in result
        assert "messages" in result


# ── Adapter factory ──────────────────────────────────────────────────────────


class TestAdapterFactoryLlamaCpp:
    def test_llama_cpp_selected_when_configured(self, monkeypatch):
        """Factory returns LlamaCppAdapter when WHOOSHD_ADAPTER=llama_cpp."""
        monkeypatch.setenv("WHOOSHD_ADAPTER", "llama_cpp")
        adapter = create_adapter()
        assert adapter.name == "llama-cpp"
        assert isinstance(adapter, LlamaCppAdapter)

    def test_default_still_stub(self, monkeypatch):
        """Default (unset) still returns stub."""
        monkeypatch.delenv("WHOOSHD_ADAPTER", raising=False)
        adapter = create_adapter()
        assert adapter.name == "stub"

    def test_mlx_still_works(self, monkeypatch):
        """MLX path is unaffected."""
        monkeypatch.setenv("WHOOSHD_ADAPTER", "mlx")
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

    def test_llama_cpp_adapter_is_protocol_compatible(self, monkeypatch):
        """LlamaCppAdapter exposes all required adapter attributes."""
        monkeypatch.setenv("WHOOSHD_ADAPTER", "llama_cpp")
        adapter = create_adapter()
        assert hasattr(adapter, "name")
        assert hasattr(adapter, "supports_streaming")
        assert hasattr(adapter, "chat_completion")
        assert hasattr(adapter, "chat_completion_stream")
        assert hasattr(adapter, "generate")
        assert hasattr(adapter, "is_loaded")
        assert hasattr(adapter, "model_id")
        assert hasattr(adapter, "warmup")
        assert hasattr(adapter, "unload")


# ── Registry metadata (model listing) ───────────────────────────────────────


class TestRegistryMetadataForGguf:
    """GGUF-backed models in the registry should carry llama_cpp metadata
    in model listings without requiring llama.cpp to be installed."""

    def test_gguf_registry_entry_engine_is_llama_cpp(self):
        """GGUF registry entries enforce llama_cpp engine via validation."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelModality,
            RegistryModelEntry,
        )

        entry = RegistryModelEntry(
            display_name="Test GGUF",
            engine=EngineType.LLAMA_CPP,
            format=ModelFormat.GGUF,
            path="/models/test.gguf",
            modalities=[ModelModality.TEXT],
        )
        assert entry.engine == EngineType.LLAMA_CPP
        assert entry.format == ModelFormat.GGUF

    def test_gguf_metadata_appears_in_openai_model_list(self):
        """OpenAI-model-list metadata includes engine=llama_cpp, format=gguf."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelRegistryConfig,
            ModelModality,
            RegistryModelEntry,
        )
        from whooshd.runtime import RuntimeState

        # Build a registry and inject it into a fresh runtime.
        registry = ModelRegistryConfig(
            models={
                "test-gguf": RegistryModelEntry(
                    display_name="Test GGUF",
                    engine=EngineType.LLAMA_CPP,
                    format=ModelFormat.GGUF,
                    path="/models/test.gguf",
                    modalities=[ModelModality.TEXT],
                    context_window=16384,
                ),
            }
        )
        rt = RuntimeState()
        rt._registry = registry  # Inject directly

        response = asyncio.run(rt.build_openai_model_list())
        assert len(response.data) == 1
        entry = response.data[0]
        assert entry.id == "test-gguf"
        assert entry.metadata is not None
        assert entry.metadata["engine"] == "llama_cpp"
        assert entry.metadata["format"] == "gguf"
        assert entry.metadata["context_window"] == 16384
        assert "text" in entry.metadata["modalities"]

    def test_gguf_details_appear_in_ollama_tags(self):
        """Ollama-tags details include format=gguf, family=llama_cpp."""
        from whooshd.registry import (
            EngineType,
            ModelFormat,
            ModelRegistryConfig,
            ModelModality,
            RegistryModelEntry,
        )
        from whooshd.runtime import RuntimeState

        registry = ModelRegistryConfig(
            models={
                "test-gguf": RegistryModelEntry(
                    display_name="Test GGUF",
                    engine=EngineType.LLAMA_CPP,
                    format=ModelFormat.GGUF,
                    path="/models/test.gguf",
                    modalities=[ModelModality.TEXT],
                ),
            }
        )
        rt = RuntimeState()
        rt._registry = registry

        response = asyncio.run(rt.build_ollama_tags())
        assert len(response.models) == 1
        tag = response.models[0]
        assert tag.name == "test-gguf"
        assert tag.model == "test-gguf"
        assert tag.details is not None
        assert tag.details["format"] == "gguf"
        assert tag.details["family"] == "llama_cpp"

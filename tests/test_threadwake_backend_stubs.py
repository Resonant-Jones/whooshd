"""Tests for backend tokenizer adapter stubs — all report unsupported or estimates_only."""

from __future__ import annotations

from whooshd.runtime.threadwake.tokenization import (
    BackendTokenizerAdapterRegistry,
    FakeTokenizerAdapter,
    ForwardingTokenizerAdapterStub,
    LlamaCppTokenizerAdapterStub,
    MlxLmServerTokenizerAdapterStub,
    MLXTokenizerAdapterStub,
    MlxVlmTokenizerAdapterStub,
    NoOpTokenizerAdapter,
    ThreadWakeTokenizerCapability,
)


class TestBackendStubs:
    def test_mlx_stub_reports_estimates_only(self):
        stub = MLXTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.ESTIMATES_ONLY

    def test_mlx_stub_tokenize_returns_not_real(self):
        stub = MLXTokenizerAdapterStub()
        result = stub.tokenize_prompt(None, None, model_id="m")
        assert result.real_tokenization is False
        assert "not_implemented" in (result.unavailable_reason or "")

    def test_llama_cpp_stub_reports_unsupported(self):
        stub = LlamaCppTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_llama_cpp_stub_tokenize_returns_not_real(self):
        stub = LlamaCppTokenizerAdapterStub()
        result = stub.tokenize_prompt(None, None, model_id="m")
        assert result.real_tokenization is False
        assert "no_local_tokenizer" in (result.unavailable_reason or "")

    def test_mlx_lm_server_stub_reports_unsupported(self):
        stub = MlxLmServerTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_mlx_vlm_stub_reports_unsupported(self):
        stub = MlxVlmTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED

    def test_forwarding_stub_reports_unsupported(self):
        stub = ForwardingTokenizerAdapterStub()
        assert stub.supports_tokenization() == ThreadWakeTokenizerCapability.UNSUPPORTED


class TestNoProductionTokenIds:
    def test_no_stub_reports_token_ids(self):
        """No production stub should falsely claim token_ids support."""
        stubs = [
            MLXTokenizerAdapterStub(),
            LlamaCppTokenizerAdapterStub(),
            MlxLmServerTokenizerAdapterStub(),
            MlxVlmTokenizerAdapterStub(),
            ForwardingTokenizerAdapterStub(),
        ]
        for stub in stubs:
            cap = stub.supports_tokenization()
            assert cap != ThreadWakeTokenizerCapability.TOKEN_IDS, (
                f"{type(stub).__name__} falsely reports token_ids"
            )
            assert cap != ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS, (
                f"{type(stub).__name__} falsely reports token_ids_with_spans"
            )

    def test_only_fake_adapter_reports_token_ids_with_spans(self):
        """FakeTokenizerAdapter is the only adapter that reports token_ids_with_spans."""
        fake = FakeTokenizerAdapter()
        assert fake.supports_tokenization() == ThreadWakeTokenizerCapability.TOKEN_IDS_WITH_SPANS


class TestRegistryReturnsNoOp:
    def test_unregistered_backend_returns_noop(self):
        reg = BackendTokenizerAdapterRegistry()
        assert isinstance(reg.get("nonexistent"), NoOpTokenizerAdapter)

    def test_stubs_not_accidentally_registered(self):
        """Ensure stubs are not registered anywhere by default."""
        reg = BackendTokenizerAdapterRegistry()
        reg.clear()
        assert reg.registered_backends() == []
        # All backends should get NoOp
        for backend in ("mlx", "llama_cpp", "mlx_lm_server", "mlx_vlm", "stub"):
            adapter = reg.get(backend)
            assert isinstance(adapter, NoOpTokenizerAdapter), (
                f"{backend} should get NoOp, got {type(adapter).__name__}"
            )

    def test_matrix_doc_exists(self):
        """Verify the backend support matrix document exists."""
        import os
        doc_path = os.path.join(
            os.path.dirname(__file__), "..", "docs", "threadwake",
            "backend-tokenizer-adapter-matrix.md",
        )
        # Normalize path
        doc_path = os.path.normpath(doc_path)
        assert os.path.exists(doc_path), f"Matrix doc not found at {doc_path}"

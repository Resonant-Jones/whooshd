"""CWC-005 negative tests for Whoosh'd logging and diagnostics."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whooshd.adapters.llama_cpp import (
    LlamaCppAdapterConfig,
    LlamaCppProcessError,
    ManagedLlamaServer,
    build_llama_server_argv,
)
from whooshd.http_forwarding import (
    _classify_request_exception,
    _safe_upstream_body,
    forward_streaming,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage


LOGGER_NAME = "whooshd.cwc005.sentinel"


def _logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def _captured_text(caplog) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME or record.name.startswith("whooshd.")
    )


def test_content_credentials_tools_media_and_paths_are_absent(caplog):
    caplog.set_level(logging.DEBUG)
    log = _logger()
    log.error(
        "request_id=%s prompt=%s assistant_completion=%s tool_arguments=%s "
        "tool_result=%s image_base64=%s stderr=%s",
        "req-cwc005",
        "USER_PROMPT_SENTINEL",
        "ASSISTANT_TEXT_SENTINEL",
        "TOOL_ARGUMENTS_SENTINEL",
        "TOOL_OUTPUT_SENTINEL",
        "IMAGE_BASE64_SENTINEL",
        "SUBPROCESS_STDERR_SENTINEL",
    )
    log.error(
        "authorization=%s cookie=%s api_key=%s url=%s",
        "Bearer BEARER_TOKEN_SENTINEL",
        "session=COOKIE_SECRET_SENTINEL",
        "API_KEY_SENTINEL",
        "https://user:pass@example.test/v1/chat?token=QUERY_SECRET_SENTINEL",
    )
    log.error(
        "model_path=%s request_id=%s runtime_kind=%s status_code=%s",
        "/private/models/SECRET_MODEL_PATH_SENTINEL.gguf",
        "req-cwc005",
        "llama_cpp",
        502,
    )
    log.info(
        "request_id=%s runtime_kind=%s model_alias=%s status_code=%s",
        "req-cwc005",
        "mlx_lm_server",
        "mlx-community/Llama-3.2-3B-Instruct-4bit",
        200,
    )

    text = _captured_text(caplog)
    for sentinel in (
        "USER_PROMPT_SENTINEL",
        "ASSISTANT_TEXT_SENTINEL",
        "TOOL_ARGUMENTS_SENTINEL",
        "TOOL_OUTPUT_SENTINEL",
        "IMAGE_BASE64_SENTINEL",
        "SUBPROCESS_STDERR_SENTINEL",
        "BEARER_TOKEN_SENTINEL",
        "COOKIE_SECRET_SENTINEL",
        "API_KEY_SENTINEL",
        "QUERY_SECRET_SENTINEL",
        "SECRET_MODEL_PATH_SENTINEL",
        "user:pass",
    ):
        assert sentinel not in text

    assert "request_id=req-cwc005" in text
    assert "runtime_kind=llama_cpp" in text
    assert "model_alias=mlx-community/Llama-3.2-3B-Instruct-4bit" in text
    assert "status_code=502" in text


def test_framework_named_logger_is_also_bounded(caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("uvicorn.error").error(
        "upstream response=%s",
        "FRAMEWORK_RESPONSE_BODY_SENTINEL",
        extra={"authorization": "Bearer FRAMEWORK_TOKEN_SENTINEL"},
    )
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "FRAMEWORK_RESPONSE_BODY_SENTINEL" not in text
    assert "FRAMEWORK_TOKEN_SENTINEL" not in text


class _StreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, lines: list[str]):
        self._lines = lines
        self.aclose = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _iter_lines(self):
        for line in self._lines:
            yield line

    def aiter_lines(self):
        return self._iter_lines()


@pytest.mark.asyncio
async def test_malformed_sse_logs_only_bounded_metadata(caplog):
    caplog.set_level(logging.DEBUG)
    valid = {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "safe-model",
        "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}],
    }
    response = _StreamResponse([
        f"data: {json.dumps(valid)}",
        "data: MALFORMED_SSE_FRAME_SENTINEL",
    ])
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.stream = MagicMock(return_value=response)
    request = ChatCompletionRequest(
        model="safe-model",
        messages=[ChatMessage(role="user", content="safe")],
        stream=True,
        request_id="req-sse-cwc005",
    )

    with patch("whooshd.http_forwarding.httpx") as httpx_mock:
        httpx_mock.AsyncClient = MagicMock(return_value=client)
        chunks = [
            chunk
            async for chunk in forward_streaming(
                "https://user:pass@example.test:9443",
                request,
            )
        ]

    assert len(chunks) == 1
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "MALFORMED_SSE_FRAME_SENTINEL" not in text
    assert "user:pass" not in text
    assert "parser_failure_class=" in text
    assert "frame_bytes=" in text
    assert "output_started=True" in text
    assert "request_id=req-sse-cwc005" in text


def test_upstream_body_and_transport_exception_are_metadata_only():
    body_sentinel = "UPSTREAM_RESPONSE_BODY_SENTINEL"
    response = MagicMock()
    response.status_code = 502
    response.headers = {"content-type": "application/json; charset=utf-8"}
    response.content = body_sentinel.encode()
    response.text = body_sentinel

    metadata = _safe_upstream_body(response)
    assert body_sentinel not in repr(metadata)
    assert metadata["upstream_status"] == 502
    assert metadata["body_bytes"] == len(body_sentinel.encode())
    assert metadata["body_present"] is True

    error = _classify_request_exception(
        RuntimeError("RAW_EXCEPTION_INTERPOLATION_SENTINEL"),
        "https://user:pass@example.test/v1/chat?api_key=URL_KEY_SENTINEL",
        3.0,
    )
    assert "RAW_EXCEPTION_INTERPOLATION_SENTINEL" not in str(error)
    assert "URL_KEY_SENTINEL" not in repr(getattr(error, "detail", {}))
    assert getattr(error, "detail", {})["failure_class"] == "runtime"
    assert getattr(error, "detail", {})["timeout_seconds"] == 3.0


def test_model_launch_logs_presence_only_and_exception_drops_stderr(caplog):
    caplog.set_level(logging.DEBUG)
    config = LlamaCppAdapterConfig(
        binary_path="/private/bin/SECRET_BINARY_PATH_SENTINEL",
        model_path="/private/models/SECRET_MODEL_PATH_SENTINEL.gguf",
        auto_start=True,
    )
    argv = build_llama_server_argv(config)
    assert argv[1] == "--model"
    text = _captured_text(caplog)
    assert "SECRET_BINARY_PATH_SENTINEL" not in text
    assert "SECRET_MODEL_PATH_SENTINEL" not in text
    assert "binary_path_present=True" in text
    assert "model_path_present=True" in text

    process = ManagedLlamaServer(config)
    with patch("os.path.isfile", return_value=True), patch(
        "subprocess.Popen",
        side_effect=OSError("SUBPROCESS_STDERR_SENTINEL /private/operator/path"),
    ):
        with pytest.raises(LlamaCppProcessError) as exc_info:
            process.start()
    assert "SUBPROCESS_STDERR_SENTINEL" not in str(exc_info.value)
    assert "/private/operator/path" not in str(exc_info.value)


def test_terminal_integrity_failure_metadata_remains_content_free(caplog):
    caplog.set_level(logging.DEBUG)
    _logger().error(
        "terminal_integrity_failure request_id=%s failure_class=%s "
        "output_started=%s frame_bytes=%s content=%s",
        "req-terminal-cwc005",
        "stream_incomplete",
        False,
        91,
        "TERMINAL_FAILURE_CONTENT_SENTINEL",
    )
    text = _captured_text(caplog)
    assert "TERMINAL_FAILURE_CONTENT_SENTINEL" not in text
    assert "req-terminal-cwc005" in text
    assert "failure_class=stream_incomplete" in text
    assert "output_started=False" in text
    assert "frame_bytes=91" in text

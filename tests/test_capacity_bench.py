from __future__ import annotations

import asyncio

import httpx

from whooshd.bench.capacity_bench import _run_one, _summarise_band, build_parser
from whooshd.bench.contracts import RequestBenchmarkResult


def _result(*, ok: bool, status_code: int | None) -> RequestBenchmarkResult:
    return RequestBenchmarkResult(
        request_index=0,
        ok=ok,
        status_code=status_code,
        stream=True,
        started_at=1.0,
        ended_at=2.0,
        total_ms=1000.0,
        ttft_ms=100.0,
        chunks=2,
        visible_chars=4,
    )


def test_overload_band_is_not_successful():
    band = _summarise_band(
        [_result(ok=False, status_code=429)],
        band_concurrency=4,
    )

    assert band.successful is False
    assert band.overload_count == 1


def test_profile_identity_and_recommendation_options_parse():
    args = build_parser().parse_args([
        "--model", "public-alias",
        "--profile-model-id", "/models/exact-revision",
        "--runtime", "mlx_vlm",
        "--machine-class", "darwin-arm64-mac16-10-m4-32gb",
        "--quantization", "affine-4bit-group64",
        "--recommended-active-concurrency", "3",
    ])

    assert args.profile_model_id == "/models/exact-revision"
    assert args.machine_class == "darwin-arm64-mac16-10-m4-32gb"
    assert args.quantization == "affine-4bit-group64"
    assert args.recommended_active_concurrency == 3


def test_streaming_run_measures_first_content_chunk_and_consumes_done():
    async def _exercise():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"A"}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"B"}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _run_one(
                client,
                base_url="http://test",
                model="m",
                prompt="p",
                max_tokens=2,
                stream=True,
                timeout=1.0,
                index=0,
            )

    result = asyncio.run(_exercise())

    assert result.ok is True
    assert result.ttft_ms is not None
    assert result.chunks == 2
    assert result.visible_chars == 2

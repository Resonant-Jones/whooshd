"""Benchmark result contracts — safe, serialisable, no prompt leakage."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RequestBenchmarkResult(BaseModel):
    """Per-request measurement."""

    request_index: int = Field(..., ge=0, description="Zero-based request number")
    ok: bool = Field(False, description="Whether the request succeeded")
    status_code: Optional[int] = Field(None, description="HTTP status code")
    stream: bool = Field(..., description="Whether this was a streaming request")
    started_at: float = Field(..., description="Unix timestamp when request began")
    ended_at: float = Field(..., description="Unix timestamp when request finished")
    total_ms: float = Field(..., ge=0, description="Total wall-clock duration in ms")
    ttft_ms: Optional[float] = Field(None, ge=0, description="Time to first content delta in ms (streaming only)")
    chunks: Optional[int] = Field(None, ge=0, description="Number of data chunks received (streaming only)")
    visible_chars: Optional[int] = Field(None, ge=0, description="Length of reconstructed visible text")
    error_code: Optional[str] = Field(None, description="Error code from structured error response")
    error_message: Optional[str] = Field(None, description="Human-readable error message")


class BenchmarkSummary(BaseModel):
    """Aggregate benchmark results."""

    ok: bool = Field(False, description="Whether all requests succeeded")
    base_url: str = Field(..., description="Target server URL")
    model: str = Field(..., description="Model ID used for requests")
    stream: bool = Field(..., description="Whether requests were streaming")
    concurrency: int = Field(..., ge=1, description="Maximum concurrent requests")
    total_requests: int = Field(..., ge=0, description="Total requests sent")
    succeeded: int = Field(0, ge=0, description="Requests with 2xx status")
    failed: int = Field(0, ge=0, description="Requests with 5xx status")
    rejected: int = Field(0, ge=0, description="Requests with 429 status")
    total_wall_ms: float = Field(0.0, ge=0, description="Total wall-clock time for all requests")
    min_latency_ms: Optional[float] = Field(None, ge=0, description="Minimum per-request latency")
    max_latency_ms: Optional[float] = Field(None, ge=0, description="Maximum per-request latency")
    mean_latency_ms: Optional[float] = Field(None, ge=0, description="Mean per-request latency")
    p50_latency_ms: Optional[float] = Field(None, ge=0, description="50th percentile latency")
    p95_latency_ms: Optional[float] = Field(None, ge=0, description="95th percentile latency")
    mean_ttft_ms: Optional[float] = Field(None, ge=0, description="Mean TTFT (streaming only)")
    p50_ttft_ms: Optional[float] = Field(None, ge=0, description="50th percentile TTFT (streaming only)")
    p95_ttft_ms: Optional[float] = Field(None, ge=0, description="95th percentile TTFT (streaming only)")
    total_visible_chars: int = Field(0, ge=0, description="Total visible characters reconstructed")
    chars_per_second: Optional[float] = Field(None, ge=0, description="Aggregate character throughput")
    errors: list[str] = Field(default_factory=list, description="Non-fatal error messages")

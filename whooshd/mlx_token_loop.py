"""MLX token-loop prototype at fake-live boundary.

Normalizes MLX lower-level generation chunks into Whoosh'd continuous
batching output shapes and routes them through the fake demux.  Does
NOT enable live continuous batching.  Missing primitives (slot ownership,
cancellation, timeout, sampling, failure isolation, cleanup) remain
explicitly unresolved.

Local/manual probe only.  🧌🥄
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from whooshd.continuous_batching import (
    ContinuousFinishReason,
    ContinuousOutputChunk,
)


class MLXTokenLoopPrototypeStatus(str, Enum):
    DISABLED = "disabled"
    PROBE_ONLY = "probe_only"
    PLAUSIBLE = "plausible"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


@dataclass(frozen=True)
class MLXTokenLoopChunk:
    request_id: str
    slot_id: str
    sequence_index: int
    text: Optional[str] = None
    finish_reason: Optional[ContinuousFinishReason] = None


MISSING_PRIMITIVES = (
    "slot_ownership",
    "cancellation_hook",
    "timeout_hook",
    "sampling_state",
    "failure_isolation",
    "cleanup_hook",
)


@dataclass(frozen=True)
class MLXTokenLoopPrototypeReport:
    backend: str = "mlx"
    status: MLXTokenLoopPrototypeStatus = MLXTokenLoopPrototypeStatus.PROBE_ONLY
    chunks_observed: int = 0
    demux_routed_chunks: int = 0
    response_order_verified: bool = False
    terminal_event_observed: bool = False
    live_path_enabled: bool = False
    adapter_behavior_changed: bool = False
    production_ready: bool = False
    generated_text_included: bool = False
    token_ids_included: bool = False
    prompt_text_included: bool = False
    missing_primitives: tuple[str, ...] = MISSING_PRIMITIVES


def normalize_mlx_stream_chunk(
    *,
    request_id: str,
    slot_id: str,
    sequence_index: int,
    text: Optional[str] = None,
    finish_reason: Optional[ContinuousFinishReason] = None,
    include_text: bool = False,
) -> ContinuousOutputChunk:
    """Normalize an MLX stream output into a Whoosh'd continuous output chunk."""
    return ContinuousOutputChunk(
        request_id=request_id,
        slot_id=slot_id,
        sequence_index=sequence_index,
        text=text if include_text else None,
        finish_reason=finish_reason,
    )


def build_mlx_token_loop_report(
    *,
    chunks_observed: int = 0,
    demux_routed_chunks: int = 0,
    response_order_verified: bool = False,
    terminal_event_observed: bool = False,
    generated_text_included: bool = False,
    status: MLXTokenLoopPrototypeStatus = MLXTokenLoopPrototypeStatus.PROBE_ONLY,
) -> MLXTokenLoopPrototypeReport:
    return MLXTokenLoopPrototypeReport(
        status=status,
        chunks_observed=chunks_observed,
        demux_routed_chunks=demux_routed_chunks,
        response_order_verified=response_order_verified,
        terminal_event_observed=terminal_event_observed,
        generated_text_included=generated_text_included,
    )

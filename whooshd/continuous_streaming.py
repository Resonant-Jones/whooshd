"""Fake streaming demux prototype — stream goblin containment chamber.

Routes fake output chunks from a continuous batching decode loop into
independent per-request stream channels.  Preserves ordering, terminal
semantics, peer isolation, and metadata-only snapshots.

Fake-runtime only.  No live HTTP streaming.  No backend.  No SSE.  🧌
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from whooshd.continuous_batching import (
    ContinuousBatchInvariantViolation,
    ContinuousFinishReason,
    ContinuousOutputChunk,
)


# ── Enums ──────────────────────────────────────────────────────────────────


class FakeStreamEventKind(str, Enum):
    CHUNK = "chunk"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class FakeStreamState(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


# ── Event / Stream / Snapshot ──────────────────────────────────────────────


@dataclass(frozen=True)
class FakeStreamEvent:
    request_id: str
    sequence_index: int
    kind: FakeStreamEventKind
    text: Optional[str] = None
    finish_reason: Optional[ContinuousFinishReason] = None


@dataclass
class FakeRequestStream:
    request_id: str
    state: FakeStreamState = FakeStreamState.OPEN
    next_sequence_index: int = 0
    events: list[FakeStreamEvent] = field(default_factory=list)
    terminal_emitted: bool = False


@dataclass(frozen=True)
class FakeStreamDemuxSnapshot:
    open_stream_count: int = 0
    completed_stream_count: int = 0
    cancelled_stream_count: int = 0
    timed_out_stream_count: int = 0
    failed_stream_count: int = 0
    total_events: int = 0
    total_chunk_events: int = 0
    total_terminal_events: int = 0


# ── Demux ──────────────────────────────────────────────────────────────────


class FakeStreamingDemux:
    """Route fake output chunks to per-request stream channels.

    Validates chunk routing, enforces sequence ordering, handles
    terminal state transitions, and preserves peer isolation.
    """

    def __init__(self) -> None:
        self._streams: dict[str, FakeRequestStream] = {}

    # ── Stream management ──────────────────────────────────────────────

    def open_stream(self, request_id: str) -> None:
        if request_id in self._streams:
            return  # Idempotent
        self._streams[request_id] = FakeRequestStream(request_id=request_id)

    def _get_or_none(self, request_id: str) -> FakeRequestStream | None:
        return self._streams.get(request_id)

    # ── Chunk routing ──────────────────────────────────────────────────

    def route_chunk(
        self,
        chunk: ContinuousOutputChunk,
        *,
        active_request_ids: set[str],
        active_slot_ids: set[str],
    ) -> list[ContinuousBatchInvariantViolation]:
        violations: list[ContinuousBatchInvariantViolation] = []

        if chunk.request_id not in active_request_ids:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
        if chunk.slot_id not in active_slot_ids:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)

        stream = self._get_or_none(chunk.request_id)
        if stream is None:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
            return violations

        if stream.state != FakeStreamState.OPEN:
            violations.append(ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED)
            return violations

        if chunk.sequence_index != stream.next_sequence_index:
            violations.append(ContinuousBatchInvariantViolation.OUTPUT_DEMUX_MISMATCH)
            return violations

        event = FakeStreamEvent(
            request_id=chunk.request_id,
            sequence_index=chunk.sequence_index,
            kind=FakeStreamEventKind.CHUNK,
            text=chunk.text,
        )
        stream.events.append(event)
        stream.next_sequence_index += 1
        return violations

    # ── Terminal state transitions ─────────────────────────────────────

    def _emit_terminal(self, request_id: str, kind: FakeStreamEventKind, reason: ContinuousFinishReason) -> list[ContinuousBatchInvariantViolation]:
        violations: list[ContinuousBatchInvariantViolation] = []
        stream = self._get_or_none(request_id)
        if stream is None:
            return violations
        if stream.state != FakeStreamState.OPEN:
            violations.append(ContinuousBatchInvariantViolation.TERMINAL_STATE_REENTERED)
            return violations

        state_map = {
            FakeStreamEventKind.COMPLETED: FakeStreamState.COMPLETED,
            FakeStreamEventKind.CANCELLED: FakeStreamState.CANCELLED,
            FakeStreamEventKind.TIMED_OUT: FakeStreamState.TIMED_OUT,
            FakeStreamEventKind.FAILED: FakeStreamState.FAILED,
        }
        stream.state = state_map.get(kind, FakeStreamState.COMPLETED)
        stream.terminal_emitted = True

        event = FakeStreamEvent(
            request_id=request_id,
            sequence_index=stream.next_sequence_index,
            kind=kind,
            finish_reason=reason,
        )
        stream.events.append(event)
        stream.next_sequence_index += 1
        return violations

    def complete(self, request_id: str) -> list[ContinuousBatchInvariantViolation]:
        return self._emit_terminal(request_id, FakeStreamEventKind.COMPLETED, ContinuousFinishReason.STOP)

    def cancel(self, request_id: str) -> list[ContinuousBatchInvariantViolation]:
        return self._emit_terminal(request_id, FakeStreamEventKind.CANCELLED, ContinuousFinishReason.CANCELLED)

    def timeout(self, request_id: str) -> list[ContinuousBatchInvariantViolation]:
        return self._emit_terminal(request_id, FakeStreamEventKind.TIMED_OUT, ContinuousFinishReason.TIMEOUT)

    def fail(self, request_id: str) -> list[ContinuousBatchInvariantViolation]:
        return self._emit_terminal(request_id, FakeStreamEventKind.FAILED, ContinuousFinishReason.ERROR)

    # ── Output ─────────────────────────────────────────────────────────

    def drain_events(self, request_id: str) -> tuple[FakeStreamEvent, ...]:
        stream = self._get_or_none(request_id)
        if stream is None:
            return ()
        events = tuple(stream.events)
        stream.events.clear()
        return events

    # ── Snapshot ───────────────────────────────────────────────────────

    def snapshot(self) -> FakeStreamDemuxSnapshot:
        counts: dict[FakeStreamState, int] = {}
        total_events = 0
        total_chunk = 0
        total_terminal = 0
        for s in self._streams.values():
            counts[s.state] = counts.get(s.state, 0) + 1
            for e in s.events:
                total_events += 1
                if e.kind == FakeStreamEventKind.CHUNK:
                    total_chunk += 1
                else:
                    total_terminal += 1
        return FakeStreamDemuxSnapshot(
            open_stream_count=counts.get(FakeStreamState.OPEN, 0),
            completed_stream_count=counts.get(FakeStreamState.COMPLETED, 0),
            cancelled_stream_count=counts.get(FakeStreamState.CANCELLED, 0),
            timed_out_stream_count=counts.get(FakeStreamState.TIMED_OUT, 0),
            failed_stream_count=counts.get(FakeStreamState.FAILED, 0),
            total_events=total_events,
            total_chunk_events=total_chunk,
            total_terminal_events=total_terminal,
        )

    # ── Validation ─────────────────────────────────────────────────────

    def validate(self) -> tuple[ContinuousBatchInvariantViolation, ...]:
        return ()  # Internal routing handles validation inline

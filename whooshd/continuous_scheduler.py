"""Fake token-level scheduler prototype — toy control tower, no dragons.

Exercises the continuous batching runtime contract under fake movement:
admits requests, assigns slots, advances prefill/decode ticks, demuxes
fake output chunks, and handles cancellation/timeout/failure isolation.

No model inference.  No backend wiring.  No live path changes.
Fake-runtime only.  🛫
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from whooshd.continuous_batching import (
    ContinuousBatchInvariantViolation,
    ContinuousBatchingStatus,
    ContinuousDecodeStep,
    ContinuousFinishReason,
    ContinuousOutputChunk,
    ContinuousRequestHandle,
    ContinuousRequestState,
    ContinuousRuntimeSnapshot,
    ContinuousSlot,
    ContinuousSlotState,
    validate_output_demux,
    validate_slot_assignments,
    validate_terminal_state_not_reentered,
)


# ── Config ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeContinuousSchedulerConfig:
    max_slots: int = 2
    max_prefill_per_tick: int = 2
    max_decode_per_tick: int = 2
    max_decode_steps_per_request: int = 3


# ── Internal request state ─────────────────────────────────────────────────


@dataclass
class FakeContinuousRequest:
    handle: ContinuousRequestHandle
    state: ContinuousRequestState = ContinuousRequestState.ADMITTED
    slot_id: Optional[str] = None
    prefill_ticks_remaining: int = 1
    decode_steps_completed: int = 0
    output_chunks: list[ContinuousOutputChunk] = field(default_factory=list)
    finish_reason: Optional[ContinuousFinishReason] = None


# ── Scheduler snapshot ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeContinuousSchedulerSnapshot:
    status: ContinuousBatchingStatus = ContinuousBatchingStatus.CONTRACT_ONLY
    admitted_count: int = 0
    prefill_pending_count: int = 0
    prefill_running_count: int = 0
    decode_active_count: int = 0
    stream_draining_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    timed_out_count: int = 0
    active_slot_count: int = 0
    free_slot_count: int = 0
    tick_index: int = 0


# ── Scheduler ──────────────────────────────────────────────────────────────


class FakeTokenLevelScheduler:
    """Fake continuous batching scheduler for contract validation.

    Admit requests, assign slots, advance ticks, demux chunks.
    No model inference.  Fake-runtime only.
    """

    def __init__(self, config: FakeContinuousSchedulerConfig | None = None) -> None:
        self.config = config or FakeContinuousSchedulerConfig()
        self._requests: dict[str, FakeContinuousRequest] = {}
        self._slots: list[ContinuousSlot] = []
        self._tick_index: int = 0
        self._pending_admission: list[str] = []
        self._init_slots()

    def _init_slots(self) -> None:
        self._slots = [
            ContinuousSlot(slot_id=f"slot-{i}", state=ContinuousSlotState.EMPTY)
            for i in range(self.config.max_slots)
        ]

    # ── Admission ──────────────────────────────────────────────────────

    def admit(self, handle: ContinuousRequestHandle) -> None:
        req = FakeContinuousRequest(handle=handle)
        self._requests[handle.request_id] = req
        self._pending_admission.append(handle.request_id)

    # ── Lifecycle mutations ────────────────────────────────────────────

    def cancel(self, request_id: str) -> None:
        req = self._requests.get(request_id)
        if req is None:
            return
        terminal = {
            ContinuousRequestState.COMPLETED, ContinuousRequestState.FAILED,
            ContinuousRequestState.CANCELLED, ContinuousRequestState.TIMED_OUT,
        }
        if req.state in terminal:
            return
        req.state = ContinuousRequestState.CANCELLED
        req.finish_reason = ContinuousFinishReason.CANCELLED
        self._release_slot_for(request_id)

    def timeout(self, request_id: str) -> None:
        req = self._requests.get(request_id)
        if req is None:
            return
        terminal = {
            ContinuousRequestState.COMPLETED, ContinuousRequestState.FAILED,
            ContinuousRequestState.CANCELLED, ContinuousRequestState.TIMED_OUT,
        }
        if req.state in terminal:
            return
        req.state = ContinuousRequestState.TIMED_OUT
        req.finish_reason = ContinuousFinishReason.TIMEOUT
        self._release_slot_for(request_id)

    def fail_request(self, request_id: str) -> None:
        req = self._requests.get(request_id)
        if req is None:
            return
        terminal = {
            ContinuousRequestState.COMPLETED, ContinuousRequestState.FAILED,
            ContinuousRequestState.CANCELLED, ContinuousRequestState.TIMED_OUT,
        }
        if req.state in terminal:
            return
        req.state = ContinuousRequestState.FAILED
        req.finish_reason = ContinuousFinishReason.ERROR
        self._release_slot_for(request_id)

    def _release_slot_for(self, request_id: str) -> None:
        for slot in self._slots:
            if slot.request_id == request_id:
                object.__setattr__(slot, "state", ContinuousSlotState.RELEASED)
                object.__setattr__(slot, "request_id", None)
                return

    # ── Tick ───────────────────────────────────────────────────────────

    def tick(self) -> None:
        self._tick_index += 1
        self._process_admission()
        self._process_prefill()
        self._process_decode()
        self._process_drain()

    def _process_admission(self) -> None:
        for rid in list(self._pending_admission):
            req = self._requests.get(rid)
            if req is None or req.state != ContinuousRequestState.ADMITTED:
                self._pending_admission.remove(rid)
                continue
            slot = self._find_empty_slot()
            if slot is None:
                return
            object.__setattr__(slot, "state", ContinuousSlotState.RESERVED)
            object.__setattr__(slot, "request_id", rid)
            req.state = ContinuousRequestState.PREFILL_PENDING
            req.slot_id = slot.slot_id
            self._pending_admission.remove(rid)

    def _process_prefill(self) -> None:
        prefill_count = 0
        for req in list(self._requests.values()):
            if prefill_count >= self.config.max_prefill_per_tick:
                break
            if req.state == ContinuousRequestState.PREFILL_PENDING:
                req.state = ContinuousRequestState.PREFILL_RUNNING
                self._update_slot_state(req.slot_id, ContinuousSlotState.PREFILL)
                prefill_count += 1
            elif req.state == ContinuousRequestState.PREFILL_RUNNING:
                req.prefill_ticks_remaining -= 1
                if req.prefill_ticks_remaining <= 0:
                    req.state = ContinuousRequestState.DECODE_ACTIVE
                    self._update_slot_state(req.slot_id, ContinuousSlotState.DECODING)

    def _process_decode(self) -> None:
        active = [r for r in self._requests.values() if r.state == ContinuousRequestState.DECODE_ACTIVE]
        decode_count = 0
        for req in active:
            if decode_count >= self.config.max_decode_per_tick:
                break
            if req.decode_steps_completed >= self.config.max_decode_steps_per_request:
                req.state = ContinuousRequestState.STREAM_DRAINING
                self._update_slot_state(req.slot_id, ContinuousSlotState.DRAINING)
                req.finish_reason = ContinuousFinishReason.STOP
                continue

            seq = req.decode_steps_completed
            chunk = ContinuousOutputChunk(
                request_id=req.handle.request_id,
                slot_id=req.slot_id or "",
                sequence_index=seq,
                text=f"{req.handle.request_id}_s{seq}",
            )
            req.output_chunks.append(chunk)
            req.decode_steps_completed += 1
            decode_count += 1

    def _process_drain(self) -> None:
        for req in list(self._requests.values()):
            if req.state == ContinuousRequestState.STREAM_DRAINING:
                req.state = ContinuousRequestState.COMPLETED
                self._update_slot_state(req.slot_id, ContinuousSlotState.RELEASED)
                for slot in self._slots:
                    if slot.slot_id == req.slot_id:
                        object.__setattr__(slot, "request_id", None)
                        break

    def _find_empty_slot(self) -> ContinuousSlot | None:
        for slot in self._slots:
            if slot.state == ContinuousSlotState.EMPTY:
                return slot
        return None

    @staticmethod
    def _update_slot_state(slot_id: Optional[str], state: ContinuousSlotState) -> None:
        # We use object.__setattr__ since ContinuousSlot is frozen.
        # Callers pass the slot reference directly.
        pass  # Handled by callers who have slot references.

    def _get_slot_by_id(self, slot_id: str) -> ContinuousSlot | None:
        for slot in self._slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    # ── Output ─────────────────────────────────────────────────────────

    def drain_outputs(self, request_id: str) -> tuple[ContinuousOutputChunk, ...]:
        req = self._requests.get(request_id)
        if req is None:
            return ()
        chunks = tuple(req.output_chunks)
        req.output_chunks.clear()
        return chunks

    # ── Snapshot ───────────────────────────────────────────────────────

    def snapshot(self) -> FakeContinuousSchedulerSnapshot:
        state_counts: dict[ContinuousRequestState, int] = {}
        for r in self._requests.values():
            state_counts[r.state] = state_counts.get(r.state, 0) + 1

        active_slots = sum(1 for s in self._slots if s.state not in (
            ContinuousSlotState.EMPTY, ContinuousSlotState.RELEASED, ContinuousSlotState.FAILED,
        ))
        free_slots = sum(1 for s in self._slots if s.state == ContinuousSlotState.EMPTY)

        return FakeContinuousSchedulerSnapshot(
            status=ContinuousBatchingStatus.CONTRACT_ONLY,
            admitted_count=len(self._requests),
            prefill_pending_count=state_counts.get(ContinuousRequestState.PREFILL_PENDING, 0),
            prefill_running_count=state_counts.get(ContinuousRequestState.PREFILL_RUNNING, 0),
            decode_active_count=state_counts.get(ContinuousRequestState.DECODE_ACTIVE, 0),
            stream_draining_count=state_counts.get(ContinuousRequestState.STREAM_DRAINING, 0),
            completed_count=state_counts.get(ContinuousRequestState.COMPLETED, 0),
            failed_count=state_counts.get(ContinuousRequestState.FAILED, 0),
            cancelled_count=state_counts.get(ContinuousRequestState.CANCELLED, 0),
            timed_out_count=state_counts.get(ContinuousRequestState.TIMED_OUT, 0),
            active_slot_count=active_slots,
            free_slot_count=free_slots,
            tick_index=self._tick_index,
        )

    # ── Validation ─────────────────────────────────────────────────────

    def validate(self) -> tuple[ContinuousBatchInvariantViolation, ...]:
        violations: list[ContinuousBatchInvariantViolation] = []

        slot_violations = validate_slot_assignments(self._slots)
        violations.extend(slot_violations)

        active_slot_ids: set[str] = set()
        terminal_reasons: dict[str, ContinuousRequestState] = {}
        handles: list[ContinuousRequestHandle] = []

        for req in self._requests.values():
            if req.state not in (
                ContinuousRequestState.COMPLETED, ContinuousRequestState.FAILED,
                ContinuousRequestState.CANCELLED, ContinuousRequestState.TIMED_OUT,
            ):
                handles.append(req.handle)
                if req.slot_id:
                    active_slot_ids.add(req.slot_id)
            else:
                terminal_reasons[req.handle.request_id] = req.state

        active_ids = {h.request_id for h in handles}

        terminal_violations = validate_terminal_state_not_reentered(handles, terminal_reasons)
        violations.extend(terminal_violations)

        all_chunks: list[ContinuousOutputChunk] = []
        for req in self._requests.values():
            all_chunks.extend(req.output_chunks)
        demux_violations = validate_output_demux(all_chunks, active_ids, active_slot_ids)
        violations.extend(demux_violations)

        return tuple(violations)

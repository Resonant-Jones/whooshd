"""Fake backend for token-step scheduler contract — sandbox bones, no fire. 🐉🏮"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence


class TokenStepSequenceState(str, Enum):
    ADMITTED = "admitted"
    PREFILL_QUEUED = "prefill_queued"
    PREFILLING = "prefilling"
    READY_TO_DECODE = "ready_to_decode"
    DECODING = "decoding"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CLEANED_UP = "cleaned_up"


@dataclass(frozen=True)
class TokenStepRequest:
    request_id: str
    prompt_label: str
    max_tokens: int
    sampler_label: str = "default"


@dataclass(frozen=True)
class TokenStepResult:
    request_id: str
    content: str
    finish_reason: str


@dataclass(frozen=True)
class TokenStepSchedulerReport:
    request_count: int = 0
    sequence_count: int = 0
    decode_steps: int = 0
    sequences_finished: int = 0
    sequences_cancelled: int = 0
    sequences_timed_out: int = 0
    sequences_failed: int = 0
    sequences_cleaned_up: int = 0
    terminal_states_observed: int = 0
    demux_routes_created: int = 0
    sampler_states_observed: int = 0
    cancellation_events_observed: int = 0
    timeout_events_observed: int = 0
    failure_events_observed: int = 0
    shared_step_failures_observed: int = 0
    demux_bleed_detected: bool = False
    cleanup_exactly_once: bool = True
    production_ready: bool = False
    performance_claim_made: bool = False
    backend: str = "fake"
    generated_text_included: bool = False
    prompt_text_included: bool = False
    token_ids_included: bool = False


@dataclass(frozen=True)
class FakeSequenceHandle:
    sequence_id: str


@dataclass(frozen=True)
class FakeCancellationPlan:
    request_id: str
    at_decode_step: int


@dataclass(frozen=True)
class FakeTimeoutPlan:
    request_id: str
    at_decode_step: int


@dataclass(frozen=True)
class FakeFailurePlan:
    request_id: str
    at_decode_step: int
    failure_type: str = "single_sequence_failure"


@dataclass(frozen=True)
class FakeSharedStepFailurePlan:
    at_decode_step: int


class FakeTokenStepBackend:
    """Deterministic fake backend for token-step scheduler contract testing."""

    def __init__(self):
        self._states: dict[str, TokenStepSequenceState] = {}
        self._max_tokens: dict[str, int] = {}
        self._step_counts: dict[str, int] = {}
        self._samplers: dict[str, str] = {}
        self._rids: dict[str, str] = {}
        self._next_id = 0

    def prefill(self, request: TokenStepRequest) -> FakeSequenceHandle:
        sid = f"seq-{self._next_id}"
        self._next_id += 1
        self._states[sid] = TokenStepSequenceState.READY_TO_DECODE
        self._max_tokens[sid] = request.max_tokens
        self._step_counts[sid] = 0
        self._samplers[sid] = request.sampler_label
        self._rids[sid] = request.request_id
        return FakeSequenceHandle(sid)

    def decode_step(self, handles: Sequence[FakeSequenceHandle]) -> dict[FakeSequenceHandle, str]:
        outputs: dict[FakeSequenceHandle, str] = {}
        for h in handles:
            sid = h.sequence_id
            state = self._states.get(sid)
            if state in (TokenStepSequenceState.CANCELLED, TokenStepSequenceState.FAILED,
                         TokenStepSequenceState.TIMED_OUT):
                continue
            if state == TokenStepSequenceState.READY_TO_DECODE:
                self._states[sid] = TokenStepSequenceState.DECODING
            step = self._step_counts.get(sid, 0)
            max_t = self._max_tokens.get(sid, 0)
            if step < max_t:
                sampler = self._samplers.get(sid, "default")
                rid = self._rids.get(sid, "?")
                outputs[h] = f"{rid}_{sampler}_{step}"
                self._step_counts[sid] = step + 1
            else:
                self._states[sid] = TokenStepSequenceState.FINISHED
        return outputs

    def cancel_sequence(self, handle: FakeSequenceHandle) -> None:
        sid = handle.sequence_id
        if self._states.get(sid) not in (TokenStepSequenceState.FINISHED,
                                          TokenStepSequenceState.CLEANED_UP,
                                          TokenStepSequenceState.CANCELLED,
                                          TokenStepSequenceState.FAILED,
                                          TokenStepSequenceState.TIMED_OUT):
            self._states[sid] = TokenStepSequenceState.CANCELLED

    def timeout_sequence(self, handle: FakeSequenceHandle) -> None:
        sid = handle.sequence_id
        if self._states.get(sid) not in (TokenStepSequenceState.FINISHED,
                                          TokenStepSequenceState.CLEANED_UP,
                                          TokenStepSequenceState.CANCELLED,
                                          TokenStepSequenceState.FAILED,
                                          TokenStepSequenceState.TIMED_OUT):
            self._states[sid] = TokenStepSequenceState.TIMED_OUT

    def fail_sequence(self, handle: FakeSequenceHandle) -> None:
        sid = handle.sequence_id
        if self._states.get(sid) not in (TokenStepSequenceState.FINISHED,
                                          TokenStepSequenceState.CLEANED_UP,
                                          TokenStepSequenceState.CANCELLED,
                                          TokenStepSequenceState.FAILED,
                                          TokenStepSequenceState.TIMED_OUT):
            self._states[sid] = TokenStepSequenceState.FAILED

    def is_finished(self, handle: FakeSequenceHandle) -> bool:
        return self._states.get(handle.sequence_id) in (
            TokenStepSequenceState.FINISHED, TokenStepSequenceState.CANCELLED,
            TokenStepSequenceState.FAILED, TokenStepSequenceState.TIMED_OUT,
        )

    def release_sequence(self, handle: FakeSequenceHandle) -> None:
        self._states[handle.sequence_id] = TokenStepSequenceState.CLEANED_UP

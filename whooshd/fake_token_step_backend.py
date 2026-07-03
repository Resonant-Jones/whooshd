"""Fake backend for token-step scheduler contract — sandbox bones, no fire. 🐉🏮"""

from __future__ import annotations

from dataclasses import dataclass
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
    prompt_label: str  # Synthetic test metadata, NOT raw prompt text
    max_tokens: int


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
    sequences_cleaned_up: int = 0
    terminal_states_observed: int = 0
    demux_routes_created: int = 0
    production_ready: bool = False
    performance_claim_made: bool = False
    backend: str = "fake"
    generated_text_included: bool = False
    prompt_text_included: bool = False
    token_ids_included: bool = False


@dataclass(frozen=True)
class FakeSequenceHandle:
    sequence_id: str


class FakeTokenStepBackend:
    """Deterministic fake backend for token-step scheduler contract testing."""

    def __init__(self):
        self._states: dict[str, TokenStepSequenceState] = {}
        self._max_tokens: dict[str, int] = {}
        self._step_counts: dict[str, int] = {}
        self._next_id = 0

    def prefill(self, request: TokenStepRequest) -> FakeSequenceHandle:
        sid = f"seq-{self._next_id}"
        self._next_id += 1
        self._states[sid] = TokenStepSequenceState.READY_TO_DECODE
        self._max_tokens[sid] = request.max_tokens
        self._step_counts[sid] = 0
        return FakeSequenceHandle(sid)

    def decode_step(self, handles: Sequence[FakeSequenceHandle]) -> dict[FakeSequenceHandle, str]:
        outputs: dict[FakeSequenceHandle, str] = {}
        for h in handles:
            sid = h.sequence_id
            if self._states.get(sid) == TokenStepSequenceState.READY_TO_DECODE:
                self._states[sid] = TokenStepSequenceState.DECODING
            step = self._step_counts.get(sid, 0)
            if step < self._max_tokens.get(sid, 0):
                outputs[h] = f"{sid[-1]}{step}"  # e.g. A0, A1, B0, B1
                self._step_counts[sid] = step + 1
            else:
                self._states[sid] = TokenStepSequenceState.FINISHED
        return outputs

    def is_finished(self, handle: FakeSequenceHandle) -> bool:
        sid = handle.sequence_id
        return self._states.get(sid) == TokenStepSequenceState.FINISHED

    def release_sequence(self, handle: FakeSequenceHandle) -> None:
        sid = handle.sequence_id
        self._states[sid] = TokenStepSequenceState.CLEANED_UP

"""Token-step scheduler contract — sandbox bones, no fire. 🐉🏮"""

from __future__ import annotations

from typing import Sequence

from whooshd.fake_token_step_backend import (
    FakeSequenceHandle,
    FakeTokenStepBackend,
    TokenStepRequest,
    TokenStepResult,
    TokenStepSchedulerReport,
    TokenStepSequenceState,
)


def run_fake_token_step_schedule(
    requests: Sequence[TokenStepRequest],
    backend: FakeTokenStepBackend,
) -> tuple[list[TokenStepResult], TokenStepSchedulerReport]:
    if not requests:
        raise ValueError("must have at least one request")

    # Prefill all requests.
    handles: list[FakeSequenceHandle] = []
    routes: dict[str, FakeSequenceHandle] = {}
    for req in requests:
        handle = backend.prefill(req)
        handles.append(handle)
        routes[req.request_id] = handle

    # Accumulate per-request outputs.
    accumulators: dict[str, list[str]] = {r.request_id: [] for r in requests}
    reverse_routes = {h.sequence_id: r.request_id for r in requests for h in [routes[r.request_id]]}

    # Decode loop.
    step_count = 0
    finished = set()
    while len(finished) < len(requests):
        active = [h for h in handles if not backend.is_finished(h)]
        if not active:
            break
        outputs = backend.decode_step(active)
        for handle, token in outputs.items():
            rid = reverse_routes.get(handle.sequence_id, "unknown")
            accumulators[rid].append(token)
        # Check finish.
        for h in active:
            if backend.is_finished(h):
                finished.add(h.sequence_id)
        step_count += 1

    # Build results.
    results = [
        TokenStepResult(
            request_id=req.request_id,
            content=" ".join(accumulators[req.request_id]),
            finish_reason="stop",
        )
        for req in requests
    ]

    # Cleanup.
    cleaned = 0
    for h in handles:
        backend.release_sequence(h)
        cleaned += 1

    report = TokenStepSchedulerReport(
        request_count=len(requests),
        sequence_count=len(handles),
        decode_steps=step_count,
        sequences_finished=len(finished),
        sequences_cleaned_up=cleaned,
        terminal_states_observed=len(requests),
        demux_routes_created=len(routes),
    )
    return results, report

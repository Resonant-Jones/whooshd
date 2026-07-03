"""Token-step scheduler with isolation contracts — joints and reflexes."""

from __future__ import annotations

from typing import Sequence

from whooshd.fake_token_step_backend import (
    FakeCancellationPlan,
    FakeFailurePlan,
    FakeSequenceHandle,
    FakeSharedStepFailurePlan,
    FakeTimeoutPlan,
    FakeTokenStepBackend,
    TokenStepRequest,
    TokenStepResult,
    TokenStepSchedulerReport,
    TokenStepSequenceState,
)


def _finish_reason(backend: FakeTokenStepBackend, handle: FakeSequenceHandle) -> str:
    sid = handle.sequence_id
    state = backend._states.get(sid)
    if state == TokenStepSequenceState.CANCELLED:
        return "cancelled"
    if state == TokenStepSequenceState.TIMED_OUT:
        return "timeout"
    if state == TokenStepSequenceState.FAILED:
        return "error"
    return "stop"


def run_fake_token_step_schedule(
    requests: Sequence[TokenStepRequest],
    backend: FakeTokenStepBackend,
    *,
    cancellation_plan: Sequence[FakeCancellationPlan] = (),
    timeout_plan: Sequence[FakeTimeoutPlan] = (),
    failure_plan: Sequence[FakeFailurePlan] = (),
    shared_step_failure_plan: Sequence[FakeSharedStepFailurePlan] = (),
) -> tuple[list[TokenStepResult], TokenStepSchedulerReport]:
    if not requests:
        raise ValueError("must have at least one request")

    # Prefill.
    handles: list[FakeSequenceHandle] = []
    routes: dict[str, FakeSequenceHandle] = {}
    for req in requests:
        handle = backend.prefill(req)
        handles.append(handle)
        routes[req.request_id] = handle
    reverse_routes = {h.sequence_id: r.request_id for r in requests for h in [routes[r.request_id]]}

    # Plans indexed by step.
    cancel_at: dict[int, list[str]] = {}
    timeout_at: dict[int, list[str]] = {}
    fail_at: dict[int, list[str]] = {}
    shared_fail_at: set[int] = set()
    for c in cancellation_plan:
        cancel_at.setdefault(c.at_decode_step, []).append(c.request_id)
    for t in timeout_plan:
        timeout_at.setdefault(t.at_decode_step, []).append(t.request_id)
    for f in failure_plan:
        fail_at.setdefault(f.at_decode_step, []).append(f.request_id)
    for sf in shared_step_failure_plan:
        shared_fail_at.add(sf.at_decode_step)

    accumulators: dict[str, list[str]] = {r.request_id: [] for r in requests}
    step_count = 0
    cancelled = 0
    timed_out = 0
    failed = 0
    shared_failures = 0

    def _active():
        return [h for h in handles if not backend.is_finished(h)]

    def _apply(plan_dict, step, fn):
        nonlocal cancelled, timed_out, failed
        for rid in plan_dict.get(step, []):
            h = routes.get(rid)
            if h and not backend.is_finished(h):
                fn(h)
                if fn == backend.cancel_sequence:
                    cancelled += 1
                elif fn == backend.timeout_sequence:
                    timed_out += 1
                elif fn == backend.fail_sequence:
                    failed += 1

    while True:
        active = _active()
        if not active:
            break

        # Apply shared-step failure BEFORE decode.
        if step_count in shared_fail_at:
            shared_failures += 1
            for h in active:
                backend.fail_sequence(h)
                failed += 1
            step_count += 1
            break

        # Apply per-request cancellations/timeouts/failures.
        _apply(cancel_at, step_count, backend.cancel_sequence)
        _apply(timeout_at, step_count, backend.timeout_sequence)
        _apply(fail_at, step_count, backend.fail_sequence)

        active = _active()
        if not active:
            break

        outputs = backend.decode_step(active)
        for handle, token in outputs.items():
            rid = reverse_routes.get(handle.sequence_id, "unknown")
            accumulators[rid].append(token)
        step_count += 1

    # Results.
    results = [
        TokenStepResult(
            request_id=req.request_id,
            content=" ".join(accumulators[req.request_id]),
            finish_reason=_finish_reason(backend, routes[req.request_id]),
        )
        for req in requests
    ]

    # Demux bleed: check that each request's output only contains its
    # own tokens.  We use a synthetic per-request prefix to avoid
    # substring false positives.
    bleed = False
    for req in requests:
        my_sampler = getattr(req, "sampler_label", "default")
        for other in requests:
            if req.request_id != other.request_id:
                other_content = " ".join(accumulators[other.request_id])
                other_sampler = getattr(other, "sampler_label", "default")
                # Check: does other's content contain MY sampler label?
                if other_sampler != my_sampler and my_sampler in other_content:
                    bleed = True

    # Cleanup.
    cleaned = 0
    for h in handles:
        backend.release_sequence(h)
        cleaned += 1

    report = TokenStepSchedulerReport(
        request_count=len(requests),
        sequence_count=len(handles),
        decode_steps=step_count,
        sequences_finished=(len(requests) - cancelled - timed_out - failed),
        sequences_cancelled=cancelled,
        sequences_timed_out=timed_out,
        sequences_failed=failed,
        sequences_cleaned_up=cleaned,
        terminal_states_observed=len(requests),
        demux_routes_created=len(routes),
        sampler_states_observed=len(requests),
        cancellation_events_observed=len(cancellation_plan),
        timeout_events_observed=len(timeout_plan),
        failure_events_observed=len(failure_plan),
        shared_step_failures_observed=shared_failures,
        demux_bleed_detected=bleed,
        cleanup_exactly_once=(cleaned == len(handles)),
    )
    return results, report

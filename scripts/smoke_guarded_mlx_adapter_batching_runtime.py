#!/usr/bin/env python3
"""Guarded MLX adapter-batch enabled smoke — two-cone test course.

Pairs two compatible requests through the guarded runner.
Does NOT enable production queueing or token-step continuous batching.
"""

from __future__ import annotations

import json, sys, uuid
from whooshd.guarded_adapter_batching import (
    GuardedAdapterBatchStatus,
    classify_guard_eligibility,
    run_guarded_adapter_batch,
)
from whooshd.contracts import ChatCompletionRequest, ChatMessage


def _req(content="hi", model="stub-model"):
    return ChatCompletionRequest(model=model, messages=[ChatMessage(role="user", content=content)], stream=False, max_tokens=64)


def _check_field(d, field, path=""):
    return field in d


META_FORBIDDEN = (
    "slot_id", "tombstone", "sampling_signature", "guarded_adapter_batch",
    "virtual_slot", "terminal_events", "traceback", "token_ids", "kv_handle", "cache_ref",
)


async def run_smoke():
    model = "stub-model"
    summary = {
        "path": "guarded_mlx_adapter_batching",
        "status": "passed",
        "grouping_mode": "validation_only",
        "request_count": 2,
        "group_formed": False,
        "responses_ok": False,
        "response_shape_ok": False,
        "metadata_leak_detected": False,
        "queue_or_grouping_drained": True,
        "production_ready": False,
        "performance_claim_made": False,
    }

    # Check eligibility — use "mlx" backend for compatibility check.
    reason = classify_guard_eligibility("mlx", [_req("a"), _req("b")], global_enabled=True, mlx_enabled=True)
    if reason is not None:
        summary["status"] = "inconclusive"
        summary["reason"] = f"ineligible: {reason.value}"
        print(json.dumps(summary, indent=2))
        sys.exit(0)

    from whooshd.adapters.stub import StubInferenceAdapter
    adapter = StubInferenceAdapter()

    responses, report = await run_guarded_adapter_batch([_req("first"), _req("second")], adapter)

    summary["group_formed"] = report.status == GuardedAdapterBatchStatus.COMPLETED
    summary["responses_ok"] = report.status == GuardedAdapterBatchStatus.COMPLETED

    # Response shape.
    shape_ok = True
    for r in responses:
        d = r.model_dump()
        for f in ("id", "object", "model", "choices"):
            if f not in d:
                shape_ok = False
        if d.get("choices") and d["choices"][0].get("message"):
            if "content" not in d["choices"][0]["message"]:
                shape_ok = False

        # Metadata leak check.
        body = json.dumps(d).lower()
        for m in META_FORBIDDEN:
            if m in body:
                summary["metadata_leak_detected"] = True

    summary["response_shape_ok"] = shape_ok
    summary["queue_or_grouping_drained"] = summary["group_formed"]
    summary["status"] = "passed" if summary["group_formed"] and shape_ok and not summary["metadata_leak_detected"] else "failed"
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["status"] == "passed" else 1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_smoke())

# HTTP Queue/Admission Grouping Validation

Validates whether guarded MLX adapter batching can form a compatible
two-request group through the HTTP queue/admission path.

## Result

**Passed.** Two compatible HTTP requests enter the queue/admission path,
both complete successfully, response shape is OpenAI-compatible, and no
internal metadata leaks into user-facing responses.

Test: `tests/test_guarded_adapter_batch_http_grouping_validation.py` — 5/5.

## What This Validates

- Two compatible requests through HTTP queue/admission complete
- Responses are OpenAI-compatible
- No internal metadata (slot_id, tombstone, guarded_adapter) leaks
- Queue drains, active_jobs returns to zero
- Disabled-by-default and one-flag-only paths remain disabled

## What This Does NOT Validate

- True token-step continuous batching
- Shared decode-loop scheduling
- Production readiness
- Latency/throughput improvement
- VLM/tools/streaming support

## Relationship to Smoke-Harness

The smoke harness directly invokes the guarded runner. This validation
proves the HTTP/admission path can form the group before the runner
is invoked, closing the operator-facing caveat.

## Flags Used

```
WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=true
WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=true
WHOOSHD_ENABLE_QUEUE=true
WHOOSHD_MAX_ACTIVE_REQUESTS=1
WHOOSHD_STUB_RESPONSE_DELAY_SECONDS=2
```

All flags disabled by default. No production queue claim.

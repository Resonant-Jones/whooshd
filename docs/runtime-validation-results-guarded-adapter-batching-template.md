# Runtime Validation Results: Guarded MLX Adapter Batching

Date:
Operator:
Machine:
OS:
Python:
Whoosh'd commit:
Branch:
Model:
Backend:
Flags:

## Scope

Validates guarded MLX adapter-batch disabled/enabled/rollback behavior.

## Non-goals

Does not validate token-step continuous batching, performance, production readiness.

## Preconditions

- [ ] Apple/MLX machine
- [ ] MLX model available locally
- [ ] Whoosh'd installed

## Startup command

```
# Fill in
```

## Disabled behavior result

- [ ] Server starts with flags unset
- [ ] Single request succeeds via existing path
- [ ] No guarded adapter-batch metadata in response

## One-flag behavior result

- [ ] Global-only: guarded path disabled
- [ ] MLX-only: guarded path disabled

## Enabled smoke result

- [ ] Two compatible requests sent
- [ ] Both return 200
- [ ] Response shape is OpenAI-compatible

## Response-shape inspection

- [ ] id present
- [ ] object = chat.completion
- [ ] model present
- [ ] choices[0].message.content present
- [ ] No slot_id, tombstone, sampling_signature, guarded_adapter in response

## Metadata/privacy inspection

- [ ] No SECRET_PROMPT in reports
- [ ] No token_ids in reports
- [ ] No traceback in user responses
- [ ] No cache_ref or kv_handle in responses

## Queue drain inspection

- [ ] Queue depth returns to zero after requests

## Failure-mode validation

- [ ] Wrong response count: covered by automated tests (test_guarded_mlx_adapter_batching.py)
- [ ] Adapter exception: covered by automated tests

## Rollback validation

- [ ] Flags unset, server restarted
- [ ] Existing path handles requests
- [ ] Guarded adapter-batch path disabled

## Results summary

Fill in: passed / failed / inconclusive

## Verdict

Fill in.

## Notes

This validation does not claim production readiness or performance improvement.
It validates guarded adapter-batch behavior only.

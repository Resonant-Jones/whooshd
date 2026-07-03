# Developer Guide

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Test Strategy

- Queue tests: `tests/test_queue.py`
- Scheduler tests: `tests/test_scheduler.py`
- Batching tests: `tests/test_guarded_mlx_adapter_batching.py`
- Fake backend tests: `tests/test_fake_token_step_*.py`
- Docs boundary tests: `tests/test_guarded_adapter_batch_operator_docs.py`

## Adding a Validation Packet

1. Create validation doc template
2. Add smoke script if needed
3. Add docs boundary test
4. Record runtime results
5. Update validation index

## Writing Claim-Safe Docs

- State what a feature IS and what it IS NOT
- Include claim boundary table
- Never claim production readiness without explicit validation
- Never claim latency/throughput improvement without benchmarks
- Distinguish adapter batching from token-step continuous batching

## PR Checklist

- [ ] Tests pass
- [ ] Docs updated
- [ ] Claim boundaries preserved
- [ ] No default enablement
- [ ] No production/performance claims without validation
- [ ] git diff --check clean

## Related

- [Architecture](architecture.md)
- [Subsystems](subsystems.md)
- [Validation Index](validation-index.md)

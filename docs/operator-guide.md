# Operator Guide

How to start, configure, validate, and roll back Whoosh'd.

## Starting

```bash
whoosh --host 127.0.0.1 --port 8000
```

## Configuration

See `whooshd/config.py` for all env vars. Key ones:

- `WHOOSHD_ADAPTER`: stub, mlx, or llama_cpp
- `WHOOSHD_MLX_MODEL`: MLX model path
- `WHOOSHD_ENABLE_QUEUE`: enable request queueing (default false)

## Guarded Adapter Batching

See [guarded-adapter-batching-operator-guide.md](guarded-adapter-batching-operator-guide.md).
Experimental, explicitly gated, disabled by default.

## Validation

See [validation-index.md](validation-index.md) and runtime validation
results in `docs/runtime-validation-results-*.md`.

## What Not to Claim

Guarded adapter batching is not production-ready, not token-step
continuous batching, and does not claim latency/throughput improvement.

See [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md).

## Troubleshooting

Check `/health` for liveness, `/ready` for readiness, `/runtime` for
full state, and server logs for bounded operational errors. See the
[Whoosh'd Logging Safety contract](security/whooshd-logging-safety.md) for
what diagnostics are retained and which external or historical log surfaces
remain unproven.

## Related

- [Manual Runtime Validation](manual-runtime-validation.md)
- [Model Management](model-management.md)
- [Configuration](configuration docs)

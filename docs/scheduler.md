# Scheduler

Whoosh'd request scheduler for queued requests.

## Policies

### FIFO (default)

Oldest queued request runs first.  No reordering.  This is the default
and the only policy that runs without explicit configuration.

### Cache-Aware FIFO (experimental)

Enabled with:

```bash
WHOOSHD_SCHEDULER_POLICY=cache_aware_fifo
```

The cache-aware policy may prefer a newer queued request with ThreadWake
cache-readiness over an older non-ready request, bounded by a fairness
bypass limit.

**Fairness guardrail:** A queued request can be bypassed at most
`WHOOSHD_SCHEDULER_MAX_BYPASS` times (default 1).  After that, FIFO
fairness forces it to the front regardless of cache readiness.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WHOOSHD_SCHEDULER_POLICY` | `fifo` | Scheduling policy: `fifo` or `cache_aware_fifo` |
| `WHOOSHD_SCHEDULER_MAX_BYPASS` | `1` | Max times a request can be bypassed by cache-aware scheduling |

## Privacy

The scheduler uses only safe, metadata-only fields:

- request ID
- enqueue timestamp
- model ID
- stream boolean
- ThreadWake cache-readiness (boolean only)
- bypass count

It never stores or exposes raw prompts, rendered prompts, message content,
generated text, token IDs, image content, KV handles, or opaque refs.

## Limitations

- Cache-aware scheduling is experimental.
- No batching or continuous batching is implemented.
- No priority lanes or user tiers.
- No performance acceleration claims are made.

## Next steps

After cache-aware scheduling is proven, the next step is batching
feasibility: can the scheduler group compatible requests for batch
execution without violating fairness or privacy?

# ThreadWake / Prefix-Cache

Warm-context layer for prompt-prefix reuse. Not AI memory.
Not persistent recall. Not a speed claim. 🧠

## Purpose

ThreadWake is Whoosh'd's prompt-prefix reuse layer. It identifies
stable prompt regions, builds deterministic cache keys, evaluates
reuse eligibility, and exposes metadata-safe observability for
prefix-cache behavior.

ThreadWake is a runtime optimization. It is not AI memory, not
identity, not long-term recall, and not a retrieval system.

## Current Status

Off by default. Supports observe-mode metadata, ephemeral and session
reuse, scope-enforced cache posture, and metadata-only observability.
Must not be described as production-ready or performance-improving
without scoped validation.

No latency or throughput improvement is claimed.

## What ThreadWake Is

- Runtime optimization
- Prompt-prefix reuse layer
- Stable-prefix classifier
- Deterministic cache-key system
- Scope-enforced cache posture
- Metadata-safe observability surface
- Optional subsystem, off by default

ThreadWake reuses compatible computed prompt-prefix state for supported
local models. It is a runtime optimization, not long-term memory.

## What ThreadWake Is Not

- Not AI memory
- Not human memory
- Not identity
- Not long-term recall
- Not persistent conversation memory
- Not a retrieval system
- Not semantic search
- Not fuzzy matching
- Not always faster
- Not a production-readiness claim
- Not a latency/throughput claim

## Prompt-Prefix Lifecycle

```
request → prompt graph compiled → stable/dynamic regions identified →
segments hashed → cache key built → eligibility evaluated →
metadata recorded → cache lookup/miss → metrics updated
```

## Modes

| Mode | Behavior |
|---|---|
| `off` | No ThreadWake processing |
| `observe` | Hash, segment, evaluate, report — no KV reuse |
| `ephemeral` | Exact stable-prefix reuse where supported |
| `session` | Monotonic conversation continuation where supported |

Mode names do not imply production readiness or performance improvement.

## Scope Boundaries

Scopes: `request`, `thread`, `project`, `user`, `global`.

Scope violations are treated as cache misses, not cross-scope reuse.
Global scope is disabled by default and requires explicit opt-in.

ThreadWake scope enforcement reduces accidental cross-context reuse,
but ThreadWake should not be described as an access-control system.

## Codexify Segment Metadata

Codexify can provide segment metadata to improve prefix identification
via `threadwake` and `threadwake_segments` fields.

Segment types: system, persona, tools, project, retrieval, thread,
user, tool_output, unknown.

Codexify segment metadata improves cacheability decisions, but invalid
metadata must not block inference.

## Queue/Admission Relationship

Queue/admission decides whether requests may proceed, wait, or be
rejected. ThreadWake does not replace admission control and must not
bypass queue/admission boundaries. See [queue-and-admission.md](queue-and-admission.md).

## Scheduler Relationship

The scheduler may use ThreadWake metadata as scheduling context where
validated. ThreadWake does not make scheduler performance claims by
itself. See [scheduler.md](scheduler.md).

## Guarded Batching Relationship

ThreadWake and guarded adapter batching are separate subsystems.
ThreadWake concerns prefix reuse. Guarded adapter batching concerns
explicitly gated request grouping. Do not describe ThreadWake as
continuous batching.

See [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md).

## Configuration

`WHOOSHD_THREADWAKE_ENABLED`, `WHOOSHD_THREADWAKE_MODE`,
`WHOOSHD_THREADWAKE_DEFAULT_SCOPE`, `WHOOSHD_THREADWAKE_MAX_ENTRIES`,
`WHOOSHD_THREADWAKE_MAX_MEMORY_MB`, `WHOOSHD_THREADWAKE_MIN_PREFIX_TOKENS`,
`WHOOSHD_THREADWAKE_ALLOW_GLOBAL`.

Request-level: `threadwake.enabled`, `.mode`, `.scope`, `.min_stable_prefix_tokens`.

Flags do not imply production readiness or performance improvement.

## Observability

`GET /health/threadwake` exposes: mode, entry counts, ready/stale
breakdown, memory estimates, hit/miss/eviction counters, backend
capabilities, entries by status and scope.

Metadata-only. Forbidden: raw prompts, generated text, user identifiers,
raw KV tensor data, opaque KV refs, tokenizer/model reprs, tracebacks,
token IDs, slot IDs, cache handles.

## Security

ThreadWake is designed for local-first single-user inference.
Raw prompt content, generated text, plaintext user identifiers,
KV state in API responses, and durable KV state must not be
exposed by default. KV cache state is sensitive even when not
human-readable. Durable snapshots remain deferred.

## Validation Coverage

- ThreadWake docs tests
- Scheduler docs tests
- Queue/admission docs tests
- Documentation spine tests
- Batching closeout tests

Validation is scoped evidence — no production/performance implication.

## Non-Goals

Implementing ThreadWake, changing cache behavior, enabling by default,
global scope defaults, AI memory claims, persistent recall, semantic
search, fuzzy matching, production readiness, latency/throughput claims,
continuous batching claims, MLX token-step claims, durable snapshots.

## Related Docs

- [threadwake/overview.md](threadwake/overview.md)
- [threadwake/configuration.md](threadwake/configuration.md)
- [threadwake/security.md](threadwake/security.md)
- [queue-and-admission.md](queue-and-admission.md)
- [scheduler.md](scheduler.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)

# ThreadWake Security & Privacy

ThreadWake is designed for **single-user local-first inference** on Apple
Silicon.  Its security posture reflects that deployment model.

---

## What ThreadWake Does Not Store

- **Raw prompt content**: Prompts are SHA-256 hashed during compilation.
  Only the hashes are stored in the metadata index.  Raw message text is
  never logged, persisted, or included in observation output.
- **Generated text**: ThreadWake processes prompt prefixes only.  Model
  output is not cached.
- **User identifiers in plaintext**: Scope identifiers (thread IDs, user
  IDs, project IDs) are SHA-256 hashed before storage.  The raw values
  are not present in the index or in observable output (health, metrics,
  logs).
- **KV state in API responses**: The `opaque_ref` field on KV handles is
  marked `exclude=True` and is excluded from all JSON serialization.
- **Durable state on disk**: ThreadWake is **process-local only**.  All
  state lives in memory and is discarded on restart.  No KV state is
  written to disk.

---

## Scope Enforcement

ThreadWake enforces strict scope boundaries on all cache lookups:

| Scope | Lookup Constraint |
|---|---|
| `request` | Matches any lookup (no constraint) |
| `thread` | `SHA-256(lookup_thread_id) == entry.scope_id` |
| `project` | `SHA-256(lookup_project_id) == entry.scope_id` |
| `user` | `SHA-256(lookup_user_id) == entry.scope_id` |
| `global` | Always matches — **disabled by default** |

**Scope violations are treated as cache misses**, not errors.  A request
from Thread B looking up Thread A's prefix will silently fall through to
full prefill — it will not return Thread A's KV state.

### Default Scope: `thread`

The default scope is `thread`.  This means:
- A request with `thread_id: "abc"` can reuse entries from prior requests
  with `thread_id: "abc"`.
- A request with no `thread_id` cannot reuse entries created with a
  `thread_id`.
- A request with `thread_id: "xyz"` cannot reuse entries from `thread_id: "abc"`.

### Global Scope

Global scope is **disabled by default** and requires explicit opt-in via
`WHOOSHD_THREADWAKE_ALLOW_GLOBAL=true`.  Global-scope entries can be
retrieved by any request regardless of thread, user, or project context.

---

## KV State Sensitivity

KV cache state is a **lossy transform** of input tokens.  While it is not
directly human-readable, theoretical reconstruction attacks exist for some
transformer architectures — a determined adversary with access to raw KV
tensors and the model weights could partially reconstruct prompt content.

ThreadWake mitigates this risk by:
1. **Keeping KV state in process memory only** — no disk persistence.
2. **Never serializing `opaque_ref`** to API responses, logs, or metrics.
3. **Running locally** — KV state never leaves the machine.

**If durable snapshots are ever implemented**, they must be:
- Encrypted at rest (AES-256-GCM)
- Bound to the machine via hardware-backed key storage (Secure Enclave)
- Integrity-verified (HMAC-SHA256) on load
- Automatically expired (TTL) and user-flushable
- Strictly opt-in with an explicit configuration flag

Currently, durable snapshots are **deferred** — see
[durable-snapshots-research.md](durable-snapshots-research.md) for the
research analysis.

---

## Flush Behavior

Flushing the ThreadWake index removes all matching metadata entries and
their associated KV handle references.

- **Flush is immediate**: Entries are removed from the in-memory index
  synchronously.
- **Flush does not crash active generation**: The flush operation only
  affects the metadata index.  Active inference requests hold their own
  KV handles and are not interrupted.
- **Flush is irreversible**: Flushed entries are not recoverable.  The
  next matching request will trigger a full prefill (cache miss).
- **Scope-filtered flush**: `POST /runtime/threadwake/flush` accepts
  optional `scope`, `model_id`, and `scope_id` filters for targeted
  eviction.

---

## Metadata & Observability

ThreadWake observation output and health endpoints contain **only**:
- Status strings (off, observing, ready, degraded)
- Counters (entry count, hit/miss/eviction totals)
- Hash values (stable prefix hash, cache key) — these are one-way SHA-256
  and do not reveal prompt content
- Token count estimates
- Memory estimates

No raw prompt text, user identifiers, or KV tensor data appears in any
observability surface.

---

## Recommendations

- **Keep global scope disabled** unless you fully understand the
  cross-context sharing implications.
- **Use observe mode first** to measure potential benefit before enabling
  KV reuse.
- **Flush the index after sensitive conversations** if you share the
  machine with other users (though scope enforcement should prevent
  cross-user hits by default).
- **Do not rely on ThreadWake for security boundaries** — it is a
  performance optimization, not an access control mechanism.

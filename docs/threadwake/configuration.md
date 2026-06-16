# ThreadWake Configuration

ThreadWake is configured via environment variables.  All settings have
conservative defaults.  ThreadWake is **off by default**.

---

## Environment Variables

### `WHOOSHD_THREADWAKE_ENABLED`

- **Default**: `false`
- **Values**: `true`, `false`, `1`, `0`, `yes`, `no`
- **Description**: Master switch.  When `false`, ThreadWake performs no
  processing and incurs zero overhead.

### `WHOOSHD_THREADWAKE_MODE`

- **Default**: `off`
- **Values**: `off`, `observe`, `ephemeral`, `session`
- **Description**: Operating mode.
  - `off` — disabled
  - `observe` — hash, segment, report metrics; no KV reuse
  - `ephemeral` — exact prefix reuse with KV cache
  - `session` — ephemeral + monotonic conversation continuation

### `WHOOSHD_THREADWAKE_DEFAULT_SCOPE`

- **Default**: `thread`
- **Values**: `request`, `thread`, `project`, `user`, `global`
- **Description**: Default scope for cache entries when a request does not
  specify one.  Global scope requires `WHOOSHD_THREADWAKE_ALLOW_GLOBAL=true`.

### `WHOOSHD_THREADWAKE_MAX_ENTRIES`

- **Default**: `16`
- **Type**: integer ≥ 1
- **Description**: Maximum number of entries in the in-memory metadata index.
  Least-recently-used entries are evicted when this limit is exceeded.

### `WHOOSHD_THREADWAKE_MAX_MEMORY_MB`

- **Default**: `1024`
- **Type**: integer ≥ 0
- **Description**: Maximum estimated memory (in MiB) for cached entries.
  Requires `WHOOSHD_THREADWAKE_BYTES_PER_TOKEN` > 0 for memory tracking to
  activate.  When exceeded, least-recently-used entries are evicted.

### `WHOOSHD_THREADWAKE_BYTES_PER_TOKEN`

- **Default**: `0` (disabled)
- **Type**: integer ≥ 0
- **Description**: Estimated bytes per token for KV cache memory modelling.
  Set to 0 to disable memory-based eviction entirely.  A reasonable starting
  value for fp16 models is `65536` (~64 KB per token for K+V across layers).
  Actual values depend on model architecture (layers, head dimension, dtype).

### `WHOOSHD_THREADWAKE_MIN_PREFIX_TOKENS`

- **Default**: `1024`
- **Type**: integer ≥ 0
- **Description**: Minimum stable prefix token estimate for eligibility.
  Requests with shorter stable prefixes are marked ineligible and skip
  cache processing.

### `WHOOSHD_THREADWAKE_ALLOW_GLOBAL`

- **Default**: `false`
- **Values**: `true`, `false`, `1`, `0`, `yes`, `no`
- **Description**: Permit global-scope cache entries.  **Must be explicitly
  enabled.**  When disabled, requests or metadata specifying global scope
  are rejected and fall back to the default scope.

---

## Request-Level Override

Individual requests can override ThreadWake settings via the `threadwake`
field in the chat completion request body:

```json
{
  "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
  "messages": [...],
  "threadwake": {
    "enabled": true,
    "mode": "ephemeral",
    "scope": "thread",
    "min_stable_prefix_tokens": 512
  }
}
```

All fields are optional.  Omitted fields fall back to the environment
variable defaults.

---

## Codexify-Specific Configuration

When using Codexify as the client, additional settings control ThreadWake
segment metadata emission (configured in Codexify, not Whoosh'd):

| Variable | Default | Description |
|---|---|---|
| `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED` | `false` | Emit segment metadata to Whoosh'd |
| `CODEXIFY_WHOOSHD_THREADWAKE_MODE` | `observe` | ThreadWake mode |
| `CODEXIFY_WHOOSHD_THREADWAKE_SCOPE` | `thread` | Default cache scope |

Metadata emission only activates when `LOCAL_PROVIDER_VENDOR=whooshd`.

---

## Health & Admin Endpoints

### `GET /health/threadwake`

Returns current ThreadWake status: mode, entry counts, hit/miss/eviction
counters, memory estimates, backend capabilities.

```json
{
  "enabled": true,
  "mode": "ephemeral",
  "status": "ready",
  "entry_count": 3,
  "ready_entries": 3,
  "stale_entries": 0,
  "max_entries": 16,
  "total_hits": 47,
  "total_misses": 12,
  "total_evictions": 2,
  "global_allowed": false
}
```

### `POST /runtime/threadwake/flush`

Flush cache metadata entries.  Accepts optional JSON body:

```json
{
  "scope": "thread",
  "model_id": "mlx-community/Llama-3.2-3B-Instruct-4bit",
  "scope_id": "my-thread-id"
}
```

All filters are optional and AND-ed.  Omit the body to flush all entries.

Response:
```json
{
  "flushed": 3,
  "remaining": 0
}
```

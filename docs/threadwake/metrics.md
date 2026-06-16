# ThreadWake Metrics & Observability

ThreadWake exposes metrics through a health endpoint and an internal
metrics registry.  All metrics are safe for logging — no raw prompt
content, user identifiers, or KV tensor data appears in metric values
or label dimensions.

---

## Health Endpoint

```
GET /health/threadwake
```

Returns a JSON snapshot of current ThreadWake state:

| Field | Type | Description |
|---|---|---|
| `enabled` | bool | Whether ThreadWake is enabled |
| `mode` | string | Current mode (off, observe, ephemeral, session) |
| `status` | string | Runtime status (off, observing, ready, degraded) |
| `entry_count` | int | Total entries in metadata index |
| `ready_entries` | int | Entries marked ready (have KV handle) |
| `stale_entries` | int | Entries marked stale (failed or invalidated) |
| `max_entries` | int | Configured maximum entries |
| `estimated_memory_bytes` | int | Estimated memory used by cached entries |
| `max_memory_bytes` | int | Configured maximum memory |
| `total_hits` | int | Cumulative cache hits |
| `total_misses` | int | Cumulative cache misses |
| `total_evictions` | int | Cumulative evictions (LRU + manual flush) |
| `global_allowed` | bool | Whether global scope is permitted |
| `backend_capabilities` | object | Map of backend name → KV capability level |
| `entries_by_status` | object | Entry count grouped by status |
| `entries_by_scope` | object | Entry count grouped by scope |

### Status Values

| Status | Meaning |
|---|---|
| `off` | ThreadWake is disabled or mode is off |
| `observing` | Observe mode: hashing and reporting, no KV reuse |
| `ready` | At least one ready entry exists; KV reuse is active |
| `degraded` | More stale entries than ready entries; cache health is poor |

---

## Internal Metrics Counters

ThreadWake maintains in-process counters for integration with metrics
backends:

### Flat Counters (no labels)

```
threadwake_observations_total           Total observations recorded
threadwake_eligible_total               Eligible requests
threadwake_ineligible_total             Ineligible requests
threadwake_estimated_reuse_tokens_total  Cumulative estimated reuse tokens
threadwake_cache_hits_total             Total cache hits (KV reuse events)
threadwake_cache_misses_total           Total cache misses
threadwake_cache_evictions_total        Total evictions
threadwake_prefix_tokens_matched_total  Cumulative matched prefix tokens
```

### Labeled Counters (bounded dimensions)

```
threadwake_hits_total{mode,scope,backend,reason}
threadwake_misses_total{mode,scope,backend,reason}
```

Allowed label values are drawn from bounded enums to prevent
high-cardinality explosion:

- **mode**: `observe`, `ephemeral`, `session`, `advanced`, `off`
- **scope**: `request`, `thread`, `project`, `user`, `global`
- **backend**: Registered backend names (e.g., `mlx`, `llama_cpp`)
- **reason**: `eligible`, `threadwake_disabled`, `prompt_graph_missing`,
  `model_id_missing`, `backend_missing`, `mode_not_supported`,
  `stable_prefix_contains_multimodal`, `stable_prefix_below_min_tokens`,
  `backend_unsupported`, `backend_unknown`, `other`

No raw hashes, user IDs, or KV refs appear as label values.

---

## Interpreting Metrics

### Hit Rate

```
hit_rate = total_hits / (total_hits + total_misses)
```

A low hit rate (< 20%) suggests:
- Prompts vary significantly between requests (different system prompts,
  different tools, different project contexts).
- The stable prefix is too short (< `min_stable_prefix_tokens`).
- The cache is frequently evicted (check `total_evictions`).

A high hit rate (> 60%) suggests:
- Stable prefixes are consistently reused.
- The cache size is adequate for your workload.

### Eviction Rate

```
eviction_rate = total_evictions / total_observations
```

A high eviction rate with a low hit rate suggests the cache is too small
for your workload — consider increasing `WHOOSHD_THREADWAKE_MAX_ENTRIES`
or `WHOOSHD_THREADWAKE_MAX_MEMORY_MB`.

### Stale Entries

Stale entries indicate KV reuse failures (clone errors, generate-from-kv
errors).  A growing stale count suggests backend KV instability.  Check
Whoosh'd logs for ThreadWake warnings.

### Eligible vs. Ineligible

A high ineligible rate means most requests don't meet the minimum stable
prefix threshold.  Consider lowering `WHOOSHD_THREADWAKE_MIN_PREFIX_TOKENS`
if your stable prefixes are genuinely short but still valuable to cache.

---

## Observability Without KV Reuse

ThreadWake in **observe** mode produces full metrics without performing
any KV reuse.  This allows you to measure the *potential* benefit before
enabling `ephemeral` or `session` mode:

1. Enable observe mode: `WHOOSHD_THREADWAKE_MODE=observe`
2. Run your workload for a representative period
3. Check `GET /health/threadwake`:
   - `eligible_count` ≈ how many requests *could* benefit
   - `estimated_prefill_reuse_tokens` ≈ how many tokens *could* be skipped
4. If eligible count and reuse tokens are high, consider switching to
   `ephemeral` or `session` mode.

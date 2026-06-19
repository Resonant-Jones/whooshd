# ThreadWake Operator Runbook

How to safely use ThreadWake observe mode, run metadata-only analysis,
read analysis reports, and interpret candidate/manifest/artifact counts.

---

## What ThreadWake Is

ThreadWake is a **runtime optimization** that identifies reusable prompt
prefixes across chat requests. When a prefix is detected, it can skip
recomputing the prefill phase — reducing latency for repeated long-context
workflows like personas, tools, project context, and thread continuation.

## What ThreadWake Is Not

- **Not AI memory.** It does not persist between restarts, does not learn
  from conversations, and does not synthesize new knowledge.
- **Not identity.** It does not track or model who you are.
- **Not persistent storage.** All cached state lives in process memory
  and is lost when Whoosh'd restarts.
- **Not currently materializing snapshots.** Analysis identifies *what would
  be worth caching* without actually caching it. No KV tensors are persisted
  to disk.

---

## Modes

| Mode | KV Reuse | Analysis | When to Use |
|---|---|---|---|
| `off` | No | No | Default. Zero overhead. |
| `observe` | No | Yes | Measure potential benefit before enabling reuse. |
| `ephemeral` | Yes | Yes | Full KV reuse for exact prefix matches. |
| `session` | Yes | Yes | Ephemeral + monotonic conversation continuation. |

Configure via `WHOOSHD_THREADWAKE_MODE` or per-request `threadwake.mode`.

---

## Visibility Surfaces

ThreadWake exposes three visibility surfaces. Each answers a different question.

| Surface | Question | Returns |
|---|---|---|
| `GET /health/threadwake` | What is ThreadWake's current posture? | Live state: mode, entry counts, hit/miss rates, KV observability, candidate registry, replay summary |
| `GET /runtime/threadwake/analysis` | What would be worth caching? | Analysis counts: candidates scanned, eligible, manifests, artifacts, errors |
| `python -m whooshd.threadwake.analyze` | Same as above, from CLI | Same analysis counts |

**Rule of thumb**: Health tells you *if* ThreadWake is working. Analysis tells you *what* it found.

---

## Recommended Operator Workflow

```
1. Confirm posture
   → GET /health/threadwake
   → Verify mode, check for errors

2. Run analysis
   → GET /runtime/threadwake/analysis
   → or: python -m whooshd.threadwake.analyze

3. Interpret counts
   → See "Interpreting Fields" below
   → Common scenarios below

4. Decide
   → candidates_eligible > 0: analysis is finding value
   → all zero: keep observing, need more data
   → errors > 0: investigate logs
   → everything looks good: no action needed
```

---

## Example Outputs

### ThreadWake off
```json
{"analysis": {"candidates_scanned": 0, ...}, "threadwake_status": {"enabled": false, "mode": "off"}}
```
**Action**: Enable observe mode to start collecting candidates.

### Observe mode, no candidates yet
```json
{"analysis": {"candidates_scanned": 0, ...}, "threadwake_status": {"enabled": true, "mode": "observe"}}
```
**Action**: Continue running workloads with stable prefixes. Candidates appear after repeated observations.

### Observe mode, eligible candidates found
```json
{"analysis": {"candidates_scanned": 8, "candidates_eligible": 3, "manifests_created": 3, "artifacts_registered": 3, "skipped": 5, "errors": 0}, ...}
```
**Action**: Analysis is finding value. Manifests and artifacts are metadata only. No KV materialization has occurred.

### Errors detected
```json
{"analysis": {"candidates_scanned": 5, "errors": 1, ...}, ...}
```
**Action**: Check Whoosh'd logs for `ThreadWake` warnings. Errors do not affect inference.

---

## Safe Operating Posture

- ThreadWake is **off by default**. Enable explicitly.
- Start with **observe mode** to measure potential benefit.
- Analysis reports are **metadata-only** — they show counts, not content.
- Visibility endpoints expose **no raw prompts, token IDs, user identifiers,
  scope IDs, opaque refs, or KV handles**.
- The analysis loop **does not call models, backends, or inference paths**.
- Durable KV snapshots are **deferred and disabled**.
- No backend is currently production-ready for snapshot materialization.

---

## Running the Analysis Report

### HTTP Endpoint

```
GET /runtime/threadwake/analysis
```

Returns:

```json
{
  "analysis": {
    "candidates_scanned": 5,
    "candidates_eligible": 3,
    "manifests_created": 3,
    "artifacts_registered": 3,
    "skipped": 2,
    "errors": 0,
    "run_count": 1
  },
  "threadwake_status": {
    "enabled": false,
    "mode": "off"
  }
}
```

### CLI

```bash
python -m whooshd.threadwake.analyze
```

Same output format.

### Health Endpoint

```
GET /health/threadwake
```

Returns live index state: entry counts, hit/miss rates, KV observability,
candidate registry summary, and replay analysis summary.

---

## Interpreting Fields

### Analysis Fields

| Field | Meaning | Healthy Range |
|---|---|---|
| `candidates_scanned` | Total candidate entries evaluated | > 0 after observe mode runs |
| `candidates_eligible` | Candidates that passed policy gates | ≤ candidates_scanned |
| `manifests_created` | Sanitized manifest records created | = candidates_eligible (if policy passes) |
| `artifacts_registered` | Metadata artifact records registered | = manifests_created |
| `skipped` | Candidates rejected by policy | high = prefixes too short, low score, or low ratio |
| `errors` | Unexpected failures during analysis | **should be 0** |
| `run_count` | Times the analysis loop has run | informational |

### Status Fields

| Field | Meaning |
|---|---|
| `enabled` | Whether ThreadWake is enabled (env or request) |
| `mode` | Current operating mode |

---

## Common Scenarios

### ThreadWake is off

```
candidates_scanned: 0, enabled: false, mode: "off"
```

**Interpretation**: ThreadWake is disabled. Enable observe mode to start
collecting candidate telemetry.

### Observe mode, collecting candidates

```
candidates_scanned: 5, candidates_eligible: 0, skipped: 5
```

**Interpretation**: ThreadWake is observing and identifying candidates,
but none yet meet the policy thresholds (minimum 5 observations, score
≥ 0.80, ratio ≥ 0.50, within 30 days). This is normal for a new workload.

### Eligible candidates, no materialization

```
candidates_eligible: 3, manifests_created: 3, artifacts_registered: 3
```

**Interpretation**: ThreadWake has identified 3 prefixes that *would be
worth caching*. Manifests and artifacts are metadata records only — no
KV state has been created. This is the expected state: analysis identifies
value, but materialization is deferred.

### High skipped count

```
candidates_scanned: 20, candidates_eligible: 2, skipped: 18
```

**Interpretation**: Most candidates don't meet policy thresholds. Common
causes: prefixes too short, seen too few times, or score too low. This
is normal for diverse workloads with short prompts.

### Errors > 0

```
candidates_scanned: 5, errors: 1
```

**Interpretation**: An unexpected failure occurred during analysis.
Check Whoosh'd logs for ThreadWake warnings. Errors do not affect
inference.

### No candidates yet

```
candidates_scanned: 0, everything else: 0
```

**Interpretation**: No candidate telemetry has been collected. Run
observe mode with a workload that has stable, repeated prefixes
(system prompts, personas, tools, project context).

---

## Safety Checklist

- [ ] ThreadWake is off by default (`WHOOSHD_THREADWAKE_ENABLED=false`)
- [ ] Observe mode used first before enabling ephemeral/session
- [ ] Analysis reports are counts-only — no raw content exposed
- [ ] No durable KV snapshots configured
- [ ] `WHOOSHD_THREADWAKE_EXPERIMENTAL_SNAPSHOTS_ENABLED=false`
- [ ] `WHOOSHD_THREADWAKE_MLX_KV_REUSE_ENABLED=false` (hard-disabled)
- [ ] `WHOOSHD_THREADWAKE_SQLITE_ENABLED=false` unless explicitly needed
- [ ] `WHOOSHD_THREADWAKE_ALLOW_GLOBAL=false`
- [ ] Analysis loop errors are 0 — investigate if not
- [ ] Health endpoint `/health/threadwake` shows expected state

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `candidates_scanned` = 0 | Is ThreadWake enabled? Is mode `observe` or higher? |
| All candidates skipped | Are prefixes long enough (>1024 tokens)? Are they repeated (≥5 times)? |
| `errors` > 0 | Check Whoosh'd logs for `ThreadWake` warnings |
| Analysis endpoint returns empty | Is the server running? Is the analysis loop initialized? |
| CLI fails | Are dependencies installed? Is `whooshd` importable? |

---

## Related Documentation

- [ThreadWake Overview](overview.md) — what ThreadWake is and how it works
- [ThreadWake Configuration](configuration.md) — all environment variables
- [ThreadWake Security](security.md) — scope enforcement, KV sensitivity, flush
- [ThreadWake Metrics](metrics.md) — health endpoint and internal counters
- [Backend Snapshot Feasibility](backend-snapshot-feasibility.md) — M19 verdict

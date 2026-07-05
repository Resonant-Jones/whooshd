# Documentation Pass Closeout Digest

One-page map of the Whoosh'd documentation anatomy pass. What was
built, what each doc is for, what claim boundaries were preserved,
and where to start.

## What Was Added

```
✅ documentation spine
✅ queue/admission deep dive       — how work enters
✅ scheduler deep dive             — how work is ordered
✅ ThreadWake / prefix-cache       — how context gets warmer
✅ guarded batching deep dive      — how compatible work rides together
✅ runtime validation deep dive    — how proof is separated from goblin theatre
```

## Documentation Map

| Area | Doc | Purpose |
|---|---|---|
| Portal | `docs/README.md` | Main entry point |
| Architecture | `docs/architecture.md` | System overview |
| Operators | `docs/operator-guide.md` | Operational guidance |
| Developers | `docs/developer-guide.md` | Dev and validation guidance |
| Subsystems | `docs/subsystems.md` | Component status map |
| Queue/admission | `docs/queue-and-admission.md` | How work enters |
| Scheduler | `docs/scheduler.md` | How eligible work is ordered |
| ThreadWake | `docs/threadwake-prefix-cache.md` | Prompt-prefix reuse |
| Guarded batching | `docs/guarded-batching.md` | Request grouping under guard conditions |
| Runtime validation | `docs/runtime-validation.md` | Scoped evidence and claims |
| Glossary | `docs/glossary.md` | Shared terms |
| Arc index | `docs/arc-index.md` | Completed engineering arcs |

## Claim Boundaries Preserved

| Claim | Status |
|---|---|
| Guarded adapter batching exists | Allowed |
| ThreadWake is prompt-prefix reuse | Allowed |
| Runtime validation is scoped evidence | Allowed |
| Token-step shared decode implemented for MLX | Not allowed |
| Guarded batching is true continuous batching | Not allowed |
| ThreadWake is AI memory | Not allowed |
| Validation proves production readiness | Not allowed |
| Latency/throughput improvement | Not claimed |
| Fake backend proof proves MLX capability | Not allowed |

## How to Use

- **New reader**: `docs/README.md` → `docs/architecture.md`
- **Operator**: `docs/operator-guide.md` → `docs/runtime-validation.md`
- **Developer**: `docs/developer-guide.md` → relevant subsystem doc
- **Batching**: `docs/batching-arc-closeout-digest.md` → `docs/guarded-batching.md`
- **ThreadWake**: `docs/threadwake-prefix-cache.md`

## What This Pass Did Not Do

No implementation changes, runtime behavior changes, default enablement,
production-readiness claims, latency/throughput claims, continuous batching
claims, AI-memory claims, MLX token-step implementation claims.

## Future Docs

Runtime adapter deep dives, model registry, configuration, metrics,
security/privacy, manual smoke cookbook, operator troubleshooting,
release-facing digest.

## Related

- [README.md](README.md)
- [architecture.md](architecture.md)
- [subsystems.md](subsystems.md)
- [arc-index.md](arc-index.md)
- [batching-arc-closeout-digest.md](batching-arc-closeout-digest.md)

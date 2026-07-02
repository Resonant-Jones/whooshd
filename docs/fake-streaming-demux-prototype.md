# Fake Streaming Demux Prototype

Stream goblin containment chamber. Fake-runtime only.

## Purpose

Routes fake output chunks from a continuous batching decode loop into
independent per-request stream channels. Validates ordering, terminal
semantics, peer isolation, and metadata-only snapshots.

## Status

Fake-runtime only. No live HTTP streaming. No SSE. No backend.

## Stream Lifecycle

```
OPEN → chunks flow → COMPLETED (terminal chunk)
                   → CANCELLED (cancel called)
                   → TIMED_OUT (timeout called)
                   → FAILED (fail called)
```

## What this proves

- Chunks route to correct request stream by request_id and slot_id
- Per-request sequence ordering is enforced
- Unknown request/slot chunks are rejected
- Terminal states (completed/cancelled/timed-out/failed) reject later chunks
- Terminal events emit exactly once
- Peer streams remain open when one stream terminates
- Snapshot is metadata-only

## What this does NOT prove

- Live HTTP streaming behavior
- Real SSE demux
- Backend integration
- Production continuous batching

## Next Steps

- Backend token-loop feasibility
- Real streaming demux behind gates

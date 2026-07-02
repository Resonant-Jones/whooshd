# MLX Token-Loop Prototype

Fake-live boundary. Real MLX chunks may enter the glass box — nothing
connects to the runway. 🧌🥄

## Status

Probe-only. No live continuous batching. MLX lower-level generation
surfaces (generate_step, stream_generate) can be normalized into
Whoosh'd continuous output chunks and routed through the fake demux.

## What it does

- Normalizes MLX stream chunks into `ContinuousOutputChunk` shapes
- Routes normalized chunks through `FakeStreamingDemux`
- Validates sequence ordering and terminal events
- Reports metadata-only with explicit missing primitives

## Missing Primitives (still unresolved)

- Slot ownership
- Cancellation hooks
- Timeout hooks
- Per-request sampling state
- Failure isolation
- Cleanup hooks

## Live Path

```
live_path_enabled = false
adapter_behavior_changed = false
production_ready = false
```

## Manual Smoke

```bash
MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
MAX_TOKENS=8 \
python scripts/probe_mlx_token_loop_prototype.py --json
```

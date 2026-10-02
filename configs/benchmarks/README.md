# Canonical Benchmark Profile Recipes

This directory contains JSON recipes for the canonical high-throughput
calibration profiles introduced by the throughput control plane.

These files are operator-friendly descriptions of what to run, what
environment variables to set, and what success looks like. They are
**not** consumed by Whoosh'd at runtime; they are documentation +
recipes.

| File | Profile | Phase |
|------|---------|-------|
| `coding-harness-queue-4.json` | 4 clients, queue depth 8, active MLX concurrency 2 | A |
| `mlx-active-concurrency-1.json` | Single-request MLX baseline | E |
| `mlx-active-concurrency-2.json` | MLX active concurrency = 2 | E |
| `mlx-active-concurrency-3.json` | MLX active concurrency = 3 | E |
| `mlx-active-concurrency-4.json` | MLX active concurrency = 4 | E |
| `mlx-batch-comparison.json` | Independent vs queued batch_generate MLX | F |

See `docs/benchmark-profiles.md` and `docs/capacity-controller.md`
for full context.
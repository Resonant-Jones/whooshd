# CWC-013 — Lane Concurrency Experiment (Isolated, Two Requests)

**Date:** 2026-11-03
**Follows:** [CWC-012 Runtime Calibration](2026-11-02-cwc012-runtime-calibration.md)
**Status: PASSED** — two concurrent requests against the 12B execute
simultaneously and efficiently, with no memory pressure or thrash.

---

## 1. Question

CWC-012 found the broker advertising `effective_active_limit: 2` while the
MLX-VLM lane's own semaphore admitted **one** request. The 12B
concurrency-band calibration was recorded as **BLOCKED**, on the reasoning
that raising the lane limit depends on the capacity evidence being sought.

That reasoning was too strong. The question is answerable by a controlled
experiment: establish safe concurrency by measurement, starting from a
conservative hypothesis, rather than by argument.

**This experiment answers it.** Raising the adapter's semaphore to 2 does not
merely *permit* two upstream requests — the measurement below shows MLX-VLM
executes them **simultaneously and efficiently**.

## 2. Method

Per the review protocol: **the working deployment on port 8000 was not
modified.** A separate broker instance was started on port 18940 with the
same registry and the same upstream runtime, differing only in the lane
concurrency limit.

| Setting | Value |
|---|---|
| Isolated broker | `http://127.0.0.1:18940` (port 8000 untouched) |
| `WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS` | `2` |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` |
| `WHOOSHD_ENABLE_QUEUE` | `true`, depth 8 |
| Upstream runtime | `mlx_vlm server` on `127.0.0.1:8082` (the launchd-managed 12B) |
| Model | `mlx-community/gemma-4-12B-it-qat-4bit` |
| Prompt | 2 048 synthetic characters |
| `max_tokens` | 128 |

A **warm-up request is issued and discarded** before the measured bands. The
model may have been evicted since it was last used, and a cold request faults
the 10 GB of weights back in from disk — producing a very large swap delta
that says nothing about concurrency. (In the first run of this experiment,
the cold band showed 341 271 swap-out pages; the warm bands showed zero. The
cold figure is a *warm-up* observation, recorded separately below, and is not
a concurrency result.)

Harness: `scripts/lane_concurrency_experiment.py`.

## 3. Results

| Metric | Band x1 | Band x2 |
|---|---:|---:|
| Successes | 1/1 | **2/2** |
| Failures | 0 | **0** |
| Aggregate tokens/sec | 6.99 | **13.24** |
| Per-request tokens/sec | 6.99 | 6.62 |
| Latency (single request) | 10 440 ms | 11 104 ms |
| Latency spread across the 2 requests | — | 0.2 ms |
| Swap pages **out** | 0 | **0** |
| Swap pages in | 296 | 418 |
| System-wide memory free after | 36 % | 35 % |

Upstream-reported for the same prompt: **349 of 365 prompt tokens cached**
(prefill 296 ms), 74 tokens generated.

Cold-start observation (discarded warm-up request): 11 440 ms, **0**
swap-out pages, 216 swap-in pages — the model was already resident.

Lifecycle cleanup after the run: `active_jobs = 0`, `queue_depth = 0`.

**Verdict: PASS** — no failed requests, no swap-out in any warm band, no
memory-pressure spike, clean lifecycle.

## 4. What this establishes

**1. Concurrency 2 is real and cheap on this machine.**

Aggregate throughput went from 6.99 to 13.24 tokens/sec — **+89 %** — while
per-request latency rose only **6.3 %** (10 440 ms → 11 104 ms). The two
concurrent requests finished within **0.2 ms of each other**, which is direct
evidence they ran simultaneously rather than being serialised behind one
another.

At concurrency 1 the machine was substantially under-utilised. The decode
loop was not the only thing running — it was waiting.

**2. There is no thrash at concurrency 2.**

Zero swap-out pages in every warm band, and system-wide free memory moved
from 36 % to 35 %. The 12B's sliding-window attention (40 of 48 layers capped
at 1024 tokens) keeps the KV footprint small enough that a second concurrent
request fits comfortably. This corroborates the CWC-012 finding that memory is
not the binding constraint on this model.

**3. Prefill remains a small fraction of the cost.**

Prefill was ~296 ms of a ~10.4 s turn — under 3 %. With 349 of 365 prompt
tokens already cached, there is very little prefill left to reclaim. **Decode
speed and concurrency are where the remaining headroom is**, not additional
prefix-cache machinery.

**4. The lane ceiling was the real constraint, and it is safe to raise.**

The CWC-012 finding that "the broker advertises 2 while the lane admits 1"
was correct, and the reason it mattered is now measured: the lane limit of 1
was leaving roughly half the machine's throughput unused, and it was the
source of the 429s seen under concurrent load.

## 5. What this does not establish

- **Not a capacity certificate for concurrency above 2.** Two is measured;
  three and beyond are not. The same harness can be run with a higher limit
  to extend the ladder.
- **Not a sustained-load result.** Each band is a short burst. Sustained
  behaviour under a real workload — hours of traffic, mixed prompt sizes —
  is a different measurement.
- **Not a claim about the fanless MacBook Air.** The M4 Air is a different
  machine with different memory and no active cooling; a concurrency figure
  measured here does not transfer.
- **Not a throughput improvement claim for Whoosh'd.** This is a
  measurement of one deployment's behaviour at two concurrency levels, not a
  before/after comparison of a code change.

## 6. Follow-ups

1. **Raise the lane limit on the working deployment.** `WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS=2` is now evidence-backed for this host and model. The working deployment still runs at the default of 1.
2. **Consider a default change.** The default of 1 for vision models is conservative. It was chosen without concurrency evidence; this packet supplies that evidence for one host.
3. **Extend the ladder** to 3 and 4 with the same harness if the fleet needs it.
4. **Re-examine the queue depth.** With the lane at 2 and the broker ceiling at 2, a third concurrent request now queues rather than being refused — the behaviour CWC-012 identified as missing.

## 7. Relationship to the lane-aware admission change

This packet measures the *runtime* side. The accompanying code change makes
the *broker* side aware of the lane: admission now caps the effective limit by
the lane's own ceiling, so the broker queues a request that the lane cannot
run instead of admitting it to stall and be refused.

That change makes the **CWC-012 429 path** unreachable for a saturated lane:
the broker absorbs the request in its queue instead. The CWC-013 measurement is
what makes raising the lane limit an informed choice rather than a guess.

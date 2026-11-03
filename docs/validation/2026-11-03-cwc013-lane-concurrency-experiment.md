# CWC-013 — Lane Concurrency Experiment (Isolated, Two Requests)

**Date:** 2026-11-03
**Follows:** [CWC-012 Runtime Calibration](2026-11-02-cwc012-runtime-calibration.md)
**Status: PASSED** — two concurrent requests against the 12B execute
simultaneously and efficiently, with no memory pressure or thrash.

> **Amended 2026-11-03 (§8).** The run recorded in §3–§4 was produced by a
> harness that could **not** prove the lane configuration it claimed to test
> and did **not** verify its own cleanup state. Those results are retained
> below as the historical record, but they are **superseded** by the
> attested re-run in §8, which carries its own figures. Read §8 before
> quoting any number from this packet.

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
model may have been evicted since it was last used, and a cold request can
produce a very large swap delta that says nothing about concurrency.

**The first run's cold band showed 341 271 swap-out pages; the warm bands
showed zero. The cause of that first-run observation is not established.**
Paging model weights *in* does not by itself explain pages being swapped
*out*, so it remains an unexplained first-run observation rather than a
diagnosed effect. The warm measurements reporting zero swap-out are useful on
their own, but they do not retroactively explain it.

The harness now separates the two: the warm-up request's swap delta is
reported under `cold_start_observation` and excluded from the concurrency
verdict.

Harness: `scripts/lane_concurrency_experiment.py`.

## 3. Results

> These are the figures from the **original** run, kept as the historical
> record. See §8 for why they are not, on their own, sufficient evidence of
> concurrency 2, and for the attested re-run that replaces them.

| Metric | Band x1 | Band x2 |
|---|---:|---:|
| Successes | 1/1 | **2/2** |
| Failures | 0 | **0** |
| Aggregate tokens/sec | 6.99 | **13.24** |
| Per-request tokens/sec | 6.99 | 6.62 |
| Latency (single request) | 10 440 ms | 11 104 ms |
| Latency spread between the 2 requests | — | 0.2 ms |
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
per-request latency rose only **6.3 %** (10 440 ms → 11 104 ms). Both
requests completed within the same wall-clock window, and their measured
durations differed by **0.2 ms**.

The harness records per-request *durations*, not absolute completion
timestamps. Near-identical durations are consistent with overlapping execution
but do not independently establish that the responses finished at precisely
the same instant, nor that GPU decoding proceeded in lockstep. The
**throughput and wall-clock evidence** — aggregate tokens/sec roughly doubling
with both requests succeeding in the same window — is what carries the
overlapping-execution conclusion.

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

## 8. Amendment — attested re-run (2026-11-03)

### 8.1 Why the original run was re-run

Code review of the experiment harness found two defects that make the §3–§4
results **insufficient on their own**:

1. **The lane configuration was never verified.** The harness printed the
   target broker's admission state and moved on. The broker publishes no
   request-specific lane ceiling, so a broker whose lane limit was still **1**
   would have answered both requests with `2xx`, serially, left `failures`
   empty, and produced a `PASS` that purported to certify concurrency two
   without having tested it.
2. **Cleanup was never verified.** The final admission read's `active_jobs`
   and `queue_depth` were recorded but never contributed to the verdict, so an
   unreachable read or leftover work still yielded `PASS`.

Both were confirmed defects in the measurement code, not in the deployment.
The conclusion of §4 was therefore never established by the §3 run alone.

### 8.2 The corrected harness

`scripts/lane_concurrency_experiment.py` now:

- requires the operator to **declare** the expected lane ceiling
  (`EXPERIMENT_EXPECTED_LANE_MAX_CONCURRENT`, plus an optional
  `EXPERIMENT_EXPECTED_LANE`) and verifies the broker's global ceilings
  (`max_active_requests`, `effective_active_limit`, `operator_ceiling`) can
  admit two at once;
- **samples live admission state** during the x2 band and records the peak
  `active_jobs` and `queue_depth`;
- **verifies cleanup at rest** — an unreachable read, a missing counter, or a
  nonzero `active_jobs` / `queue_depth` fails or un-verifies the run;
- distinguishes **PASS / FAILED / UNVERIFIED**. Missing or unreachable
  evidence is `UNVERIFIED`, never `PASS`.

The harness aborts before the bands when the target cannot be attested, and
re-attests afterwards.

### 8.3 The attested re-run

Same model, same upstream runtime, same isolation rule — **the working
deployment on port 8000 was not modified**. A separate broker instance ran on
port 18923 with the corrected code.

| Setting | Value |
|---|---|
| Isolated broker | `http://127.0.0.1:18923` (port 8000 untouched) |
| `WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS` | `2` |
| `WHOOSHD_MAX_ACTIVE_REQUESTS` | `2` |
| `WHOOSHD_ENABLE_QUEUE` | `true`, depth 8 |
| Upstream runtime | `mlx_vlm server` on `127.0.0.1:8082` (the launchd-managed 12B) |
| Model | `mlx-community/gemma-4-12B-it-qat-4bit` |
| Declared expected lane ceiling | `2` |

**Attestation: `ATTESTED`** — broker reachable, global ceilings
`max_active_requests = 2`, `effective_active_limit = 2`,
`operator_ceiling = 2`, **191 admission samples** during the x2 band with
**`peak_active_jobs = 2`** and **`peak_queue_depth = 0`**.

| Metric | Band x1 | Band x2 |
|---|---:|---:|
| Successes | 1/1 | **2/2** |
| Failures | 0 | **0** |
| Aggregate tokens/sec | 8.218 | **13.661** |
| Per-request tokens/sec | 8.218 | 6.830 |
| Latency (p50) | 9 368.2 ms | 10 785.1 ms |
| Latency (max) | 9 368.2 ms | 10 979.3 ms |
| **Observed peak active jobs** | 1 | **2** |
| **Observed peak queue depth** | 0 | **0** |
| Swap pages **out** | 0 | **0** |
| Swap pages in | 12 | 281 |

Upstream-reported for the same prompt: **349 of 365 prompt tokens cached**
(prefill 283.5 ms), 74 tokens generated.

Cold-start observation (discarded warm-up request): 12 519.9 ms, **0**
swap-out pages, 532 swap-in pages.

Lifecycle cleanup after the run: **verified** — `reachable = true`,
`active_jobs_at_rest = 0`, `queue_depth_at_rest = 0`.

**Verdict: PASS** — `failures: []`, `unverified: []`.

### 8.4 What the attested run establishes

**1. Concurrency 2 is real.** Unlike the original run, this one carries
positive evidence: the broker's own admission state was observed at
`active_jobs = 2` with `queue_depth = 0` during the x2 band. The two
requests were demonstrably in flight **together** and neither was queued
behind the other — which is precisely what the original harness could not
distinguish from two serial successes.

**2. The throughput gain is smaller than the original run reported.**
Aggregate throughput rose from 8.218 to 13.661 tokens/sec — **+66 %** — while
per-request latency rose **15.2 %** (9 368 ms → 10 785 ms). The original run
reported **+89 %** at **+6.3 %** latency. The direction and the conclusion are
unchanged — concurrency 2 leaves roughly half the machine's throughput unused
at concurrency 1 — but **the magnitude is not reproducible** and the original
`+89 %` figure should not be quoted. The re-run is the figure of record.

**3. No thrash, and the lifecycle drains.** Zero swap-out pages in both warm
bands; cleanup verified with `active_jobs_at_rest = 0` and
`queue_depth_at_rest = 0`, reachable rather than assumed.

### 8.5 What the attested run does not establish

Everything in §5 still holds, unchanged. In particular this remains a
**single short burst**, not a sustained-load result, and it is **not** a
capacity certificate for concurrency above 2.

The **341 271 swap-out pages** observed in the original first run remain an
**unexplained first-run observation**. The re-run's warm-up recorded 0
swap-out and 532 swap-in pages; that is consistent with a resident model and
does not retroactively explain the original event.

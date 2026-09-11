# CWC-007 Throughput Control Plane — Final Engineering Report

**Branch:** `codex/cwc-007-throughput-control-plane`

**Validation host:** Mac mini `Mac16,10`, Apple M4 (10 cores), 32 GB RAM,
macOS 26.5.2

**Validation date:** 2026-08-19

**Last requalification:** 2026-09-11

```text
Automated implementation validation: COMPLETE
Live stub validation: COMPLETE
Live real-MLX plumbing validation: COMPLETE
Target Gemma 4 12B capacity calibration: PENDING
```

The throughput control plane has been validated against a real MLX runtime
using Gemma 4 E2B. Gemma 4 12B IT QAT capacity certification remains a
model-specific target-host benchmark and does not block validation of the
control-plane mechanics.

This is infrastructure validation, not model-quality evaluation.

## 1. Certification boundary

| Layer | Fixture | Judgment |
|---|---|---|
| Control-plane / real-MLX plumbing | Gemma 4 E2B IT 4-bit | Complete |
| Cross-model portability smoke | Gemma 4 E4B IT 4-bit | Blocked by installed checkpoint/runtime incompatibility |
| Production-capacity calibration | Gemma 4 12B IT QAT 4-bit | Pending |

The E2B result certifies request transport, admission, queueing, scheduler
selection, capacity gating, runtime concurrency, MLX generation, streaming,
cancellation, response isolation, and lifecycle cleanup. It is not a 12B
throughput recommendation.

## 2. Branch hygiene

Before live validation, the four unrelated backend-request-policy changes were
removed from this branch and committed independently:

```text
unrelated-work branch: codex/backend-request-policy-work
commit: 81fe385
files moved:
  configs/models.friends-family-guest.yaml
  tests/test_backend_request_policy.py
  whooshd/app.py
  whooshd/backend_request_policy.py
throughput branch clean before MLX validation: yes
```

The path `.venv311/` is excluded only through the repository-local
`.git/info/exclude`. The environment was not modified or deleted, and the
tracked `.gitignore` was not changed.

## 3. Runtime identity

The E2B fixture was absent locally, so the existing MLX-compatible instruct
variant was downloaded once and pinned to an immutable Hugging Face revision.
No alternative model was downloaded.

```text
public/registry model ID: mlx-community/gemma-4-e2b-it-4bit
runtime: mlx_vlm
local snapshot:
  /Volumes/Dev_SSD/whooshd/model-weights/hub/
  models--mlx-community--gemma-4-e2b-it-4bit/snapshots/
  238767527555cb75a05732a84dff5d6ba0dd6809
quantization: affine 4-bit, group size 64
advertised text context window: 131072 tokens
checkpoint verification: 10 files verified
```

The model was served by an isolated `mlx_vlm` sidecar on port 18082 and the
current branch by an isolated Whoosh'd process on port 18000. The existing
launchd-managed Whoosh'd and Qwen runtime were not changed.

## 4. Implementation corrections found by real-runtime validation

Real execution exposed two control-plane defects that stub validation did not:

1. **CANCELLATION:** a late streaming completion could overwrite an already
   cancelled lifecycle record with `completed`. Completion now preserves the
   terminal `cancelled`, `failed`, and `timed_out` states. A focused
   regression test covers the race.
2. **PROFILE_IDENTITY / CAPACITY:** profiles were structurally round-tripped but
   were not strictly bound to the selected model/runtime/host. Adaptive mode now
   requires exact model ID, runtime, machine class, and host-memory identity.
   A mismatch rejects the calibration and falls back to the operator ceiling.

Supporting changes also make the configured real runtime/model authoritative in
capacity observability and make `capacity-bench` measure SSE TTFT at the first
received data chunk instead of after buffering the whole response. The profile
format remains the existing `CapacityProfile`; no second calibration format was
introduced.

New capacity identity inputs are:

| Variable | Purpose |
|---|---|
| `WHOOSHD_CAPACITY_MODEL_ID` | Immutable model identity or local snapshot |
| `WHOOSHD_CAPACITY_RUNTIME` | Exact runtime/backend identity |
| `WHOOSHD_CAPACITY_MACHINE_CLASS` | Stable operator-declared host class |

Fixed mode remains the backward-compatible default. Adaptive mode never raises
the limit above `WHOOSHD_MAX_ACTIVE_REQUESTS`.

## 5. Single-request baseline

### Non-streaming

```text
submitted/completed: 1 / 1
rejected/failed/stuck: 0 / 0 / 0
latency: 1372.319 ms
response contract: valid
usage: prompt 17, completion 4, total 21 tokens
active_jobs after completion: 0
queue_depth after completion: 0
```

### Streaming

```text
submitted/completed: 1 / 1
rejected/failed/stuck: 0 / 0 / 0
TTFT: 187.971 ms
latency: 267.433 ms
framing: valid SSE chunks, terminal finish_reason, then [DONE]
completion truncated: no
active_jobs after completion: 0
queue_depth after completion: 0
```

## 6. Active concurrency matrix

The primary matrix used short, constant prompts. TTFT and latency below are
from the streaming run. Token throughput is from a companion non-streaming run,
where actual completion token counts were available; character throughput was
not substituted.

| Active band | Submitted | Completed | Queued | Rejected | Failed | Stuck | TTFT p50 / p95 ms | Latency p50 / p95 ms | Aggregate token throughput | Mean request token throughput | Active / queue peak | Memory |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| x1 | 1 | 1 | 0 | 0 | 0 | 0 | 205.695 / 205.695 | 825.741 / 825.741 | 31.562 tok/s | 31.575 tok/s | 1 / 0 | normal, about 4.2 GB reported |
| x2 | 2 | 2 | 0 | 0 | 0 | 0 | 244.348 / 264.440 | 537.901 / 703.336 | 77.802 tok/s | 38.914 tok/s | 2 / 0 | normal |
| x3 | 3 | 3 | 0 | 0 | 0 | 0 | 327.866 / 328.374 | 673.030 / 1016.504 | 80.241 tok/s | 29.221 tok/s | 3 / 0 | normal |
| x4 | 4 | 4 | 0 | 0 | 0 | 0 | 388.306 / 389.000 | 844.476 / 1038.745 | 76.109 tok/s | 23.713 tok/s | 4 / 0 | normal |

All bands returned their own deterministic marker, ended with
`active_jobs=0` and `queue_depth=0`, and left the runtime semaphore usable.
x4 is operationally correct, but x3 delivered the best measured aggregate
token throughput. Therefore maximum functioning concurrency and recommended
concurrency are intentionally different.

## 7. Coding-harness bursts and request isolation

| Workload | Completed | Queued / dequeued | Rejected / failed / stuck | TTFT p50 / p95 ms | Latency p50 / p95 ms | Active / queue peak |
|---|---:|---:|---:|---:|---:|---:|
| 4 clients / 2 active / queue 8 | 4 / 4 | 2 / 2 | 0 / 0 / 0 | 365.739 / 1497.143 | 1315.262 / 2229.231 | 2 / 2 |
| 8 clients / 2 active / queue 8 | 8 / 8 | 6 / 6 | 0 / 0 / 0 | 1275.185 / 3320.510 | 2051.438 / 4070.497 | 2 / 6 |
| 8 clients / 4 active / queue 8 | 8 / 8 | 4 / 4 | 0 / 0 / 0 | 473.831 / 2060.872 | 1853.024 / 3081.979 | 4 / 4 |

Every burst completed without unexplained 429 responses, duplicate execution,
lost requests, or stuck lifecycle state. `REQUEST_A` through `REQUEST_D` and
the extended burst markers were checked in both streaming and non-streaming
paths: each response contained its own marker and no foreign marker. All burst
runs ended with active and queue counters at zero.

## 8. Cancellation under real MLX

Queued and active cancellation were both exercised with real work:

```text
queued cancellation signalled: true
queued client result: HTTP 409
queued marker reached MLX/output: no
queued final lifecycle: cancelled

active cancellation signalled: true
active stream: HTTP 200, 0 content chunks, terminal [DONE]
active final lifecycle: cancelled

follow-up request: HTTP 200, correct marker
final active_jobs / queue_depth: 0 / 0
```

The runtime path supports cooperative interruption of active streaming
generation. Queue selection remained valid, the queue continued draining, and
there was no semaphore leak or orphaned queue entry.

## 9. Context pressure

Prompt sizes were measured with the E2B tokenizer rather than inferred from
characters.

| Context band | Measured prompt tokens | Active concurrency | Result | Latency / TTFT observation | Memory observation |
|---|---:|---:|---|---|---|
| ~2K | 2,001 | x1 | complete | 4.220 s non-streaming | normal |
| ~8K | 8,001 | x1 | complete | 7.579 s non-streaming | normal |
| ~16K | 16,001 | x1 | complete | 12.334 s non-streaming | normal |
| ~32K | 32,001 | x1 | complete | 24.501 s non-streaming | normal; about 47% free |
| ~64K | 64,001 | x1 | complete | 56.961 s non-streaming | normal; about 41% free |
| ~2K | 2,001 | x2 | complete | TTFT p95 7.264 s | normal |
| ~2K | 2,001 | x4 | complete | TTFT p95 14.318 s | normal |
| ~16K | 16,001 | x2 | complete | TTFT p95 23.719 s | normal |
| ~16K | 16,001 | x4 | complete | TTFT p95 47.233 s | swap activity observed |
| ~32K | 32,001 | x2 | complete | TTFT p95 47.691 s | normal at finish; about 46% free |

All executed combinations completed without rejection, failure, or lifecycle
leak. The campaign stopped before `32K x4` and `64K x2/x4`: tail TTFT had
already become severe and swapouts increased by about 2.35 GiB during the
matrix. That is a safety stop and useful capacity evidence, not a control-plane
failure. The nominal 128K limit was not attempted.

## 10. CapacityProfile, isolation, and restart

The existing CLI produced:

```text
profile path:
  /Volumes/Dev_SSD/whooshd/validation/cwc-007-e2b/
  capacity-profile.gemma-4-e2b-it-4bit.json
profile model identity:
  /Volumes/Dev_SSD/whooshd/model-weights/hub/
  models--mlx-community--gemma-4-e2b-it-4bit/snapshots/
  238767527555cb75a05732a84dff5d6ba0dd6809
runtime: mlx_vlm
machine class: darwin-arm64-mac16-10-m4-32gb
host memory: 34359738368 bytes
quantization: affine-4bit-group64
recommended active concurrency: 3
operator ceiling: 4
effective concurrency after restart: 3
reason: calibrated_limit
restart consumption verified: yes
```

The four-request-per-band profile run completed x1/x2/x3/x4 with no failures,
rejections, or stuck requests. Because it was streaming and the runtime did not
return usage counts, its throughput fields remain `null`; the separate
non-streaming matrix with real token counts is the basis for choosing x3.

Operator-ceiling proof after a clean restart:

```text
profile recommendation: 8
operator ceiling: 4
effective concurrency: 4
reason: operator_ceiling
```

The canonical artifact was then restored to the evidence-based recommendation
of 3. Corrupt and invalid profiles continue to use the conservative fallback.

Strict profile-isolation proof:

| Selected runtime identity | Profile eligible | Effective result |
|---|---|---|
| Exact E2B snapshot + `mlx_vlm` + matching host | yes | calibrated limit 3 |
| Installed E4B snapshot + `mlx_vlm` | no | `model_mismatch`; operator fallback |
| Installed 12B snapshot + `mlx_vlm` | no | `model_mismatch`; operator fallback |

An E2B calibration therefore cannot silently become the active limit for E4B
or 12B.

## 11. Optional E4B portability smoke

The locally installed E4B checkpoint was attempted but did not reach readiness.
`mlx_vlm` rejected 126 checkpoint parameters because the runtime-instantiated
model lacked the checkpoint's layers 24–41. Classification:
`MLX_RUNTIME` / `MODEL` compatibility. No request was sent, no model files were
changed, and this optional smoke does not weaken the completed E2B plumbing
proof. It remains an explicit portability follow-up rather than being reported
as a passing result.

## 12. Recommendation

These values are specific to Gemma 4 E2B on the measured Mac mini:

```text
Recommended E2B active inference concurrency: 3
Recommended coding-harness client concurrency: 4
Recommended queue depth: 8
```

x4 is mechanically sound for short prompts, and eight clients queue and drain
correctly. x3 is the recommended active limit because aggregate token
throughput peaked there while x4 reduced per-request throughput and long-context
x4 materially worsened TTFT and induced swap activity.

Production roles remain distinct:

```text
Gemma 4 E2B = control-plane / MLX validation fixture
Gemma 4 E4B = optional portability fixture
Gemma 4 12B IT QAT 4-bit = production-capacity calibration target
```

## 13. Rebase requalification — 2026-09-11

The branch was rebased from `968ea35` onto current shared main
`6d02b3f` (`origin/main`). Local `main` was not rewritten. The only manual
integration resolution retained both newer request-ID provenance and the
qualification-attestation section in `docs/request-contract.md`; no source
conflict required a hand merge.

The existing exact E2B fixture and profile were reused without modification:

```text
model: mlx-community/gemma-4-e2b-it-4bit
revision: 238767527555cb75a05732a84dff5d6ba0dd6809
runtime: mlx_vlm
profile recommendation: 3
operator ceiling: 4
```

Fresh-process profile consumption passed before inference:

```text
profile_loaded: true
profile_eligible: true
profile_rejection_reason: null
effective_active_limit: 3
capacity_reason: calibrated_limit
```

A bounded four-client streaming burst exercised streaming, queue lifecycle,
and request isolation together:

```text
submitted / completed: 4 / 4
queued / dequeued: 1 / 1
rejected / failed / stuck: 0 / 0 / 0
active concurrency peak / queue peak: 3 / 1
TTFT p50 / p95: 1898.518 / 2578.759 ms
latency p50 / p95: 2399.199 / 3484.284 ms
all streams received terminal reason and [DONE]: yes
all expected request markers present: yes
cross-talk: no
active_jobs / queue_depth after completion: 0 / 0
```

The same E2B profile was then checked after a separate restart with the
installed 12B path as the configured capacity identity. The 12B model was not
loaded and no 12B inference was attempted; this was an identity-rejection
check only.

```text
profile_loaded: true
profile_eligible: false
profile_rejection_reason: model_mismatch
calibrated_concurrency: null
effective_active_limit: 4
fallback boundary: operator ceiling
```

The rebased changed-scope automated suite passed `229` tests. Both isolated
qualification processes were stopped afterward, and the pre-existing Whoosh'd
service remained healthy. These results requalify the E2B control-plane
mechanics after integration; they do not widen the original model-specific
capacity claim.

## 14. Validation status

Automated tests cover profile schema/round-trip, strict identity matching,
operator ceilings, queue/scheduler authority, runtime lifecycle, cancellation,
SSE benchmark timing, admission, and batch interaction.

```text
focused changed-scope suite: 175 passed
final focused regression slice: 58 passed
full suite: 2271 passed, 17 failed
```

The 17 failures are the pre-existing ThreadWake route/snapshot-policy and
vision-routing failures documented before this campaign. No new failure is in
the throughput-control-plane scope. A sandboxed full-suite attempt was also
discarded before collection because Metal was unavailable there; the reported
run used the host Metal device.

```text
Control-plane implementation: COMPLETE
Real MLX plumbing: COMPLETE
Gemma 4 E2B capacity calibration: COMPLETE
Gemma 4 12B production calibration: PENDING
```

Continuous batching, production ThreadWake KV reuse, and 12B optimization
remain out of scope. The pipe is validated; the production pump still needs its
model-specific capacity certificate.

# Changelog

## v0.1.1 (Unreleased)

> Follows the repository's release convention established in `1e3f480`: the top
> section carries the version being prepared and stays marked unreleased until
> the tag is cut. `v0.1.0` is tagged and released; work after it lives here.

### Fixed

- **Route-specific capacity reservation.** The broker's global limit and the
  inference lane's own limit were two independent admission decisions, and the
  broker could admit a request that then stalled at the lane's semaphore and
  was eventually refused — which defeated the purpose of broker-level
  queueing. Each adapter may now declare its own concurrency ceiling; the
  broker's effective limit for a request bound for that lane is capped by it,
  so the request is queued rather than admitted to stall. The global limit is
  not lowered for other lanes — a strict lane reserves capacity for itself
  without constraining independent lanes.
  The route is now resolved **before** admission, so the lane is known when the
  decision is made. A saturated lane's refusal names the real constraint
  instead of the global limit.
  Tests: `tests/test_capacity_lane_reservation.py`.

- **Lane-aware admission did not hold end to end.** The first implementation
  had two defects that the controller tests did not expose, because they proved
  the decisions individually rather than the complete
  admission → queue → release → execution contract.
  - **Queued requests could still bypass the lane limit.** The queue released
    work on global capacity alone, so a request whose lane was saturated was
    granted execution and then stalled at the adapter's semaphore — the exact
    failure the feature exists to prevent. The lane is now re-checked at
    release time.
  - **Unrelated lanes were incorrectly constrained.** The effective limit was
    computed as `min(global, lane)` and compared against the *global* active
    counter. That starved an idle lane whenever any other lane was busy, and
    let a saturated lane start work the global budget had room for. Global
    capacity and lane capacity are now separate constraints over separate
    counters, both of which must hold.
  - The queue-full check now runs *after* the availability check. A request
    that can run immediately is no longer refused because the queue happens to
    be full — which matters now that the queue legitimately holds
    lane-saturated work while global capacity sits free.
  - **`/v1/generate` is now lane-aware too.** It previously used a simple
    global-only check and did not consult the capacity controller at all. Both
    routes now resolve the adapter before admitting, so the selected lane's
    ceiling is known when the decision is made.
  - Integration regressions cover both scenarios end to end, plus the
    queue-release path and the generate route:
    `tests/test_lane_admission_integration.py`. Verified to fail against the
    pre-fix code.

- **Multimodal context cost was undercounted.** The estimator supported a
  per-unit multimodal cost, but the live admission path never supplied it, so
  image content was counted as zero tokens. The profile's
  `multimodal_token_estimate` is now wired into admission, and content whose
  cost cannot be determined is reported as an **unknown cost** rather than
  zero: `context_cost_has_unknown` marks it and `context_cost_is_exact`
  reports that the hard-limit comparison for that request is best-effort
  rather than a guarantee.

- **Context budgets were selected by the wrong model.** Budget lookup keyed
  on the process-wide `WHOOSHD_CAPACITY_MODEL_ID`, so a broker serving
  several models applied one model's hard ceiling to a request routed to
  another. Budgets are now looked up by the **resolved model that will
  execute the request**. A request whose model has no measured budgets is
  admitted unchanged — an unmeasured model is never silently constrained.

- **The effective output limit was not reserved.** `max_completion_tokens` is
  an alias for `max_tokens`, but a client that set only `max_completion_tokens`
  left the request model carrying the *default* `max_tokens=256`, and the
  estimator reserved 256 output tokens while the broker forwarded the larger
  limit. The reservation now follows the **larger** of `max_tokens` and
  `max_completion_tokens` — an upper bound on what the upstream may generate,
  since the upstream's precedence between the two fields is not knowable from
  here.

- **Forwarded prompt-affecting fields were not counted.** Tool definitions,
  assistant `tool_calls`, structured-output schemas, tool choice, stop
  sequences, and message `name` / `tool_call_id` are all forwarded upstream
  and all occupy prompt space, but the estimator counted only
  `message.content`. A request carrying a large tool set passed a hard
  budget it could never fit. Every forwarded field is now counted.

- **The profile's declared per-direction maxima were never enforced.**
  `max_input_tokens` and `max_output_tokens` below the combined hard ceiling
  had no effect: a 16 K-input request was admitted against a 32 K hard
  ceiling even with an 8 K input maximum declared, and an output request
  above `max_output_tokens` was forwarded unchanged. Each direction is now
  compared against its own declared limit and a breach is rejected. The
  estimator no longer silently clamps the output reservation to the model's
  maximum — clamping would rewrite the client's request without telling it.

- **Cost precision was not propagated on every evaluation.** The interactive
  tier omitted the exactness flag, and a declared per-unit multimodal
  *estimate* was reported as if it were a measurement. Evaluations now carry
  a tri-state `context_cost_status` — `exact`, `estimated`, or `unknown` —
  populated on **every** branch including the no-budget path, alongside
  `context_cost_is_exact` and `context_cost_has_unknown`.

- **A grant transferred ownership but not capacity.** Under
  `cache_aware_fifo`, the front waiter could observe a free slot, remove a
  *different* entry (a cache-affinity candidate), and only then signal it.
  The grant transferred queue ownership but not capacity: `active_jobs` did
  not include the granted request until its coroutine resumed, so a request
  arriving in that scheduling gap was admitted into the same slot. A grant
  now **reserves** the execution slot synchronously with the removal, and the
  reservation is held — against both global and lane capacity — until the
  request actually starts running. The scheduler also checks the
  *candidate's* own lane before granting, so a cache-affinity selection can
  never hand work to a saturated lane.
  Regression tests: `tests/test_queue_capacity_reservation.py` (4 tests),
  verified to fail against the pre-fix code.

- **Not every guarded adapter declared its concurrency ceiling.** MLX-LM
  Server and llama.cpp enforce a semaphore limit but did not expose it, so
  admission could not respect it. Both now declare
  `max_concurrent_requests`, the same limit their semaphore enforces.

- **The lane concurrency experiment could certify a concurrency it never
  tested.** The broker's admission read was printed but never validated, and
  the broker publishes no request-specific lane ceiling — so a broker whose
  lane limit was still 1 would answer two requests with 2xx each, serially,
  leaving `failures` empty and producing a `PASS` that purported to certify
  concurrency two. The harness now **positively attests** the target's lane
  configuration from operator declaration plus observable broker state, and
  samples live admission state during the x2 band; an unattestable or
  unreachable target is `UNVERIFIED`, never `PASS`. Final cleanup state is
  verified too: an unreachable or nonzero `active_jobs` / `queue_depth` at
  rest now fails or un-verifies the run instead of being recorded and
  ignored.
  Regression tests: `tests/test_lane_concurrency_experiment.py` (34 tests).

- **The calibration harness measured the wrong thing, and could not fail.**
  Seven defects in `whooshd.bench.runtime_calibration`, all confirmed
  against the code and all now corrected:
  - **Every band launched the same number of concurrent requests.** The
    `band` argument was never used to throttle either measurement phase,
    so with the defaults the x1, x2, x3 and x4 rows all exercised eight
    simultaneous requests and their latency, overload and throughput
    figures were mislabeled. Both phases are now gated by a semaphore of
    the band's size, and the band reports the concurrency it actually
    exercised.
  - **A missing upstream probe crashed the Markdown path.** The optional
    Markdown renderer indexed cache fields that an unconfigured probe does
    not define, so a default run wrote its JSON and then failed with
    `KeyError`. The absent fields are now present and absent values render
    as absent.
  - **A truncated stream counted as a success.** A 2xx response that ended
    early, or that carried an SSE error frame, was recorded as a successful
    request even though it had no terminal completion. A streaming request
    now succeeds only when the response was 2xx **and** the terminal
    `[DONE]` marker was observed.
  - **Queue latency was advertised but never populated.** The metric is
    accepted → running and needs both timestamps from the broker's own
    lifecycle records. The broker publishes an accepted timestamp but no
    running-transition timestamp, so the harness now reports the field as
    unavailable with a stated reason instead of leaving it silently empty.
  - **A failed cache probe was reported as measured evidence.** A 4xx/5xx
    response, an unparsable body, or a payload without usage incremented
    the attempt counter before any validation, and the probe was then
    reported as available with zero cached tokens. A probe is available
    only when the upstream answered successfully *and* returned valid usage
    data; failures are counted with their reason.
  - **An unreachable leak check was recorded as clean.** The final
    lifecycle read was coerced through `or 0`, so an unreachable broker
    produced `leaked: false` — an affirmative no-leak result from an
    unmeasured state. The at-rest leak check now reports `unknown` when
    the broker was unreachable or a counter was missing.
  - **A failed token-accounting phase did not fail the band.** Streaming
    successes were counted while non-streaming failures were skipped, so a
    band could be marked successful on partial throughput evidence. Both
    phases must now complete; a partial phase is reported as not measured
    and the band is not successful.
  Regression tests: `tests/test_runtime_calibration.py` (30 tests), verified
  to fail against the pre-fix harness. **28 of the 30 failed before the
  correction.**

- **Four lifecycle-boundary defects the corrections themselves exposed.**
  The lane-aware admission and the context-policy work made the guarantees
  more precise, and the precision gaps that created were found on the
  resulting commit:

  - **Media tokens bypassed the input-token ceiling.** When a profile
    declared both `multimodal_token_estimate` and `max_input_tokens`, the
    ceiling was compared with the textual/structured input only. A request
    with 1 000 text tokens and a 5 000-token image was admitted against a
    4 096-token input maximum whenever its combined total stayed below the
    hard ceiling. The input ceiling now covers the model's **whole** input:
    text, structured prompt fields, and multimodal content.
  - **Granted reservations inflated queue depth.** A request removed from
    the scheduler queue by a cross-waiter grant remained `QUEUED` while
    holding a reservation, so `RuntimeState.queue_depth` over-reported
    occupancy — and a new request could be refused as *queue full* while
    the real queue still had room. Granted request ids are now excluded
    from `queue_depth` and from the oldest-queued age. The reservation
    still holds its capacity slot, so excluding it from the queue count
    does not free capacity.
  - **The experiment's upstream probe lacked response validation.** A JSON
    error envelope with a 4xx/5xx status, or a 2xx payload without usage
    data, was still recorded as an *available* probe with absent counters.
    The probe now requires a successful status **and** valid usage data,
    and records a failure reason otherwise. The same function also read the
    upstream's decode speed from `timings.generation_per_second`, which the
    upstream does not publish — it reports `predicted_per_second` — so the
    measured decode rate was always absent. Both are corrected.
  - **Cross-waiter grants could exceed queue deadlines.** A candidate
    selected on behalf of another waiter was granted even when its **own**
    queue deadline had already passed, so it executed after the timeout
    that was supposed to bound it. Every entry now carries a deadline
    stamped at enqueue time; expired candidates are excluded before the
    scheduler chooses, and an owner whose grant arrives after its own
    deadline treats it as a timeout rather than executing.

  Regression tests: `tests/test_lifecycle_boundary_findings.py` (15 tests) —
  **10 failed against the pre-fix commit**, all passing after.

- **Three further defects raised against `94c9049`:**
  - **Model-budget matching could conflate repositories.** The identity
    comparison fell back to the final path segment, so a profile for
    `org-a/foo` was applied to a request resolved to `org-b/foo` — one
    model's context ceiling applied to another, which can wrongly reject
    valid requests or admit work beyond the real model's ceiling. Identity
    is now compared **with its repository**: the Hugging Face snapshot
    layout (`models--org--name`) is canonicalised to `org/name` rather than
    truncated, and where the namespace cannot be confirmed on both sides the
    identities are not treated as equal. A local snapshot path and its
    repository id are still recognised as the same model.
  - **Per-request throughput used band wall time.** The experiment harness
    computed a band's per-request rate by dividing its average token count
    by the whole band's wall clock, which is effectively the slowest
    request's duration — so faster requests were underreported. The rate is
    now the mean of each request's own `completion_tokens / latency_seconds`,
    as the runtime-calibration harness already did. The published CWC-013
    per-request figures were recomputed from the recorded per-request data;
    the aggregate row is unaffected, because aggregate throughput genuinely
    is band wall time.
  - **An invalid profile was re-parsed on every request.** When
    `WHOOSHD_CAPACITY_PROFILE_PATH` pointed at a malformed or schema-invalid
    profile, the loader only reused the cache for a *successful* load, so
    every chat and generate request re-read and re-validated the same
    broken file — turning a persistent configuration error into
    request-path disk I/O. The failure is now cached against the same
    path+mtime signature, and retried when the file changes.

  Regression tests: `tests/test_round3_findings.py` (14 tests) — **7 failed
  against the pre-fix commit**, all passing after.

- **Queue/scheduler execution-ownership defect.** Under
  `WHOOSHD_SCHEDULER_POLICY=cache_aware_fifo`, `select_and_dequeue()` could
  remove a request that the calling waiter did not own. The removed request
  had no owner executing it — it neither ran nor remained queued — while the
  front caller also failed to execute. Selection now grants the removed
  entry to its own waiting caller, which is woken directly; a waiter that
  loses the selection keeps its place instead of abandoning its request.
  Behaviour under the default `fifo` policy is unchanged.
  Regression tests: `tests/test_queue_execution_ownership.py`.

### Added

- **Per-model context policy.** `CapacityProfile` gains optional
  `context_budgets` carrying hard / recommended / interactive token ceilings.
  Profiles without them degrade to the legacy ceiling.
- **Request-level token and memory costing.** New `whooshd.capacity_context`
  estimates the assembled cost of a request — input tokens, reserved output
  tokens, and multimodal content — and compares it against the model's
  budgets. `CapacityController.evaluate()` remains the single decision point.
  A request over the hard budget is rejected with `context_overflow`; one
  over the recommended budget is admitted with a recorded advisory reason. A
  model with no calibrated budgets is never silently constrained.
- **Real-runtime calibration harness.** `whooshd.bench.runtime_calibration`
  measures a live deployment — TTFT, upstream-reported prefill cost,
  throughput, queue latency, host swap activity, prompt-cache effectiveness,
  and lifecycle leak at rest. An unmeasurable value is reported as absent,
  never as zero.
- **Calibration launchd job and operator guide.** `scripts/calibrate_runtime.sh`
  and `.local/launchd/com.resonant.whooshd-calibration.plist` run the
  measurement outside an agent sandbox, where Metal is reachable.

### Measured

- **CWC-013 lane concurrency experiment (isolated, 12B) — two attested
  re-runs.** With the MLX-VLM lane limit raised to 2 on **isolated brokers**
  — the working deployment on port 8000 was not modified — two concurrent 12B
  requests both succeeded, twice, with **zero** swap-out pages in every warm
  band and a verified clean lifecycle (`active_jobs_at_rest = 0`,
  `queue_depth_at_rest = 0`, reachable) in both runs.
  Both runs are **attested**: the harness sampled the broker's own admission
  state during the x2 band and observed `peak_active_jobs = 2` with
  `peak_queue_depth = 0`, so the two requests are demonstrably in flight
  together and neither was queued behind the other.
  **The gain is a range, not a figure.** Aggregate throughput rose **+66 %**
  in one run (8.218 → 13.661 tok/s, per-request latency +15.2 %) and **+102 %**
  in the other (7.383 → 14.920 tok/s, per-request latency −1.4 %) — two runs
  minutes apart on the same host and checkpoint. The honest statement is:
  **concurrency 2 roughly doubles aggregate throughput, with a per-request
  latency cost somewhere between −1 % and +15 %.** Any single percentage
  overstates how repeatable the number is.
  An earlier run of the same experiment reported **+89 %** at **+6.3 %**
  latency, but its harness could neither attest the lane configuration it
  claimed to test nor verify its own cleanup state — a broker whose lane
  limit was still 1 would have produced the same `PASS`. That figure is
  superseded and is not quoted.
  The direction is unchanged and is the conclusion that survives: the lane
  limit of 1 was leaving roughly half the machine's throughput unused.
  Packet: `docs/validation/2026-11-03-cwc013-lane-concurrency-experiment.md`
  (§8).

- **CWC-012 runtime calibration (Gemma 4 12B, real Metal).** TTFT ~497 ms
  warm, prefill ~275 ms, ~7 tokens/sec at `max_tokens=128`, and a **0.956
  cached prompt-token fraction** (698 of 730 prompt tokens). Prefill is under
  3 % of turn latency, so decode speed and concurrency — not further
  prefix-cache machinery — hold the remaining headroom. The cache figure is a
  cached-token fraction for one repeated synthetic prompt, **not** a
  request-level hit rate across normal workloads. Packet:
  `docs/validation/2026-11-02-cwc012-runtime-calibration.md`.

### Evaluated

- **MLX batch execution — deferred.** Existing experimental work (guarded
  adapter batching, the `batch_generate` capability probe, the
  `supports_chat_batching` signal) was evaluated rather than redesigned. No
  change to the shipped path. Note:
  `docs/mlx-batch-execution-evaluation.md`.

### Not scheduled

- **Multi-node routing** — demand-conditional. Host discovery, authenticated
  network access, model availability, and routing authority are the
  consequential blockers. A configurable state directory is a convenience for
  several instances on *one* host; two installations on different machines
  already have separate `~/.whooshd` directories.
- **Embeddings / tool calling** — demand-conditional. Embeddings need an
  embedding model; tool calling needs an execute-versus-pass-through decision.

---

## v0.1.0 (Unreleased)

First GA release. Delta from `v0.1.0rc3` (222 commits), grouped by subsystem.

> The `(Unreleased)` marker follows the repository's release convention
> established in `1e3f480`: the top section carries the version being prepared
> and stays marked unreleased until the tag is cut.

### Capacity control plane

New bounded throughput control plane. The `CapacityController` answers one
question — how many requests may execute right now — and delegates admission,
queue, and scheduler decisions to it.

- `CapacityController` abstraction and `CapacityProfile` artifact
- `fixed` (default) and `adaptive` modes; adaptive may only **lower** the
  effective active limit, never above the `WHOOSHD_MAX_ACTIVE_REQUESTS` ceiling
- Six `WHOOSHD_CAPACITY_*` configuration variables
- Strict model/host identity binding — a profile is eligible only on exact
  match of model id, runtime, machine class, and host memory
- Non-fatal conservative fallback: `missing_profile`, `invalid_profile`,
  `model_mismatch` all degrade to the operator ceiling
- `GET /runtime/capacity` metadata-only snapshot
- `python -m whooshd.bench.capacity_bench` per-band calibration CLI
- Scheduler-authoritative `select_and_dequeue`; admission delegates capacity
  decisions to the controller

### Request correlation

- Bounded v1 correlation: upstream `X-Request-ID` and Whoosh'd
  `X-Whoosh-Request-ID` stored and propagated as distinct identities
- Preserved through immediate, streaming, queued, cancelled, pre-stream
  failure, and mid-stream error paths
- Cancellation by the Whoosh'd ID returns the associated correlation pair
- Unsafe or oversized identifiers omitted, never reflected
- Note: an earlier **unbounded** propagation design was reverted; the shipped
  implementation is this bounded contract

### Control-plane v1 contract

- `whooshd.control.v1` versioned request/response negotiation
- Structured error envelopes with stable codes, retryability, and bounded
  retry timing
- Streaming terminal integrity: post-output failures emit a canonical SSE error
  event and never fabricate a successful `[DONE]`
- Shared repository-neutral v1 contract fixture corpus

### Runtime provenance and qualification

- Bounded `whooshd.runtime.v1` provenance on model inventory and successful
  responses; streaming via `X-Whooshd-Runtime-Provenance`
- Target-scoped, additive `qualification_attestation` reference
- External adapter model paths bound explicitly; external routes kept behind a
  registry allowlist

### Model registry

- Explicit authoritative runtime registry — an operator-owned, fail-closed model
  allowlist
- Unknown and disabled model IDs no longer fall through to adapter heuristics
- Guest/friends-family launch profiles

### Logging and safety

- Bounded Whoosh'd-owned log records under a documented safety contract
- Daemon ownership verification and log sanitization
- Unavailable runtime probes handled; readiness and batch errors fail closed
- Historical files, platform logs, and external collectors are not claimed to
  be retroactively scrubbed

### Daemon and runtime recovery

- Verified legacy daemon group recovery, including BSD process-group lookup
- Warmup/readiness stabilization and runtime failure boundary clarification
- launchd runtime bundle as the operator path

### Documentation

- `docs/capacity-controller.md` and `docs/reports/throughput-control-plane.md`
- Rebuilt `docs/README.md` spine, `docs/operator-guide.md`, and
  `docs/api-reference.md`
- `docs/validation-index.md` reconciled with the recorded evidence, preserving
  PASSED / FAILED / INCONCLUSIVE / BLOCKED / PENDING distinctions
- `docs/security/whooshd-logging-safety.md`

### Verified

- 2360 passed, 3 skipped (full suite)
- 37 capacity control plane tests passing
- Real MLX control-plane plumbing validated against Gemma 4 E2B on Apple M4 /
  32 GB, including admission, queueing, scheduler selection, capacity gating,
  streaming, cancellation, request isolation, and lifecycle cleanup

### Pending

- **Gemma 4 12B production capacity calibration** — target host
  (`Mac16,10` M4 / 32 GB) and a complete local checkpoint are present, but the
  benchmark run was not performed. Not a GA blocker.
- **Gemma 4 E4B cross-model portability smoke** — blocked by an
  `mlx_vlm` / checkpoint parameter incompatibility.

### Known Limitations

- No production-ready continuous batching or token-step shared decode for MLX
  (Cave Thunder decision)
- No throughput or latency improvement claim beyond the recorded validation
  packets
- Production ThreadWake KV reuse not enabled; backends report `unsupported`
  capability by default
- Durable KV snapshots deferred
- No embeddings, tool calling, multi-node routing, or production auth hardening
- Capacity profiles are model/host-bound and are not portable recommendations

---

## v0.1.0rc3

### Changed

- `python -m whooshd.threadwake.analyze` now fetches live analysis from the
  running Whoosh'd daemon instead of constructing a fresh empty in-memory
  ThreadWake manager.

---
## Optional Bounded Request Queue — Phase 4B (2026-06-26)

Feature-flagged bounded FIFO request queue behind `WHOOSHD_ENABLE_QUEUE`.

### Added

- Bounded FIFO request queue (disabled by default; `WHOOSHD_ENABLE_QUEUE=false`)
- Queue env config: `WHOOSHD_ENABLE_QUEUE`, `WHOOSHD_MAX_QUEUE_DEPTH` (8),
  `WHOOSHD_QUEUE_TIMEOUT_SECONDS` (120), `WHOOSHD_QUEUE_POLL_INTERVAL_MS` (25)
- `QUEUED` and `TIMED_OUT` request lifecycle states
- Queue observability via `GET /runtime/admission` (counters and depth)
- Cancel/timeout queued requests without invoking the adapter
- Streaming: no SSE chunks emitted while queued
- 50 queue tests covering disabled behavior, enqueue/dequeue, queue full,
  timeout, cancellation, streaming hold, and no-leakage guarantees

### Unchanged

- Default reject-only 429 behavior preserved
- OpenAI-compatible API contract unchanged
- No priority lanes, batching, prompt-prefix caching, ThreadWake KV reuse,
  embeddings, tool calling, or durable snapshots

## Runtime Readiness Smoke Layer (2026-06-19)

Tag: `whooshd-runtime-readiness-smoke-2026-06`

Turned the README quickstart into executable artifacts with live verification.

### Added

- Example env files: `examples/env.stub`, `examples/env.codexify`,
  `examples/env.mlx.example`, `examples/env.llama-cpp.example`
- Smoke scripts: `scripts/smoke_stub.sh`, `scripts/smoke_threadwake.sh`,
  `scripts/smoke_openai_compat.sh`
- Live stub smoke verification: 12/12 checks passing

### Docs

- README runtime surface map, local sanity checklist, Codexify connection notes,
  ThreadWake milestone status table, troubleshooting guide

---

## ThreadWake Metadata Milestone (2026-06-19)

Tag: `threadwake-metadata-milestone-2026-06`

Pre-materialization platform layer for prompt-prefix reuse optimization.

### Added

- **Metadata spine** (M14–M18): artifact registry, snapshot creation gate,
  material contract, material validation, backend materialization interface
- **Backend feasibility investigation** (M19): no backend is production-ready
  for real KV snapshot materialization
- **Metadata-only analysis loop** (M20): periodic analysis outside inference path
- **Read-only visibility surface** (M21): `GET /runtime/threadwake/analysis` +
  `python -m whooshd.threadwake.analyze`
- **Operator runbook** (M22): safe usage, field interpretation, scenarios,
  safety checklist
- **Visibility docs polish** (M23): "which surface?" table, operator workflow,
  example outputs
- **Documentation index** (M24): consolidated threadwake docs map, milestone
  status, safety boundary

### Safety

- KV materialization: **not enabled**
- Durable snapshots: **deferred**
- Production restore: **not implemented**
- Backend materialization: no backend supports it
- All visibility surfaces: counts and status only — no raw prompts, token
  IDs, opaque refs, or user identifiers

### Scope

- 24 source modules, 47 test files, 10 doc pages, 3 benchmark scripts
- 614 tests passing, 4 skipped

---

## v0.1.0rc2 (2026-05-29)

### Added

- Release hygiene tag capturing the configured-model inventory alignment and successful live Codexify integration proof.

## v0.1.0rc1 (2026-05-17)

### Added

- OpenAI-compatible `POST /v1/chat/completions` (non-streaming and SSE streaming)
- `GET /v1/models` (OpenAI format) and `GET /api/tags` (Ollama format)
- `GET /health` (liveness) and `GET /ready` (readiness, 200/503)
- `GET /runtime` and `GET /runtime/model` (lifecycle snapshots)
- `POST /runtime/model/warmup` and `POST /runtime/model/unload`
- `GET /runtime/requests` and `POST /runtime/requests/{id}/cancel`
- `GET /runtime/admission` (limits and counters)
- `POST /v1/generate` (Codexify-style generation)
- Optional `mlx-lm` backend with lazy loading and streaming via `stream_generate`
- Stub inference adapter (always available, no dependencies)
- Adapter factory with `WHOOSHD_ADAPTER=stub|mlx` selection
- Request lifecycle tracking with `active_jobs` computed from live state
- Cancellation signaling with per-request `CancellationToken`
- Adapter-aware cancellation (stub and MLX stream cooperation)
- Admission control with configurable limits (`429 RUNNER_OVERLOADED`)
- Throughput benchmark harness (`python -m whooshd.bench.runner`)
- Codexify provider smoke probe (`python -m whooshd.compat.probe_server`)
- SSE stream parser (`reconstruct_assistant_text`) for Codexify compatibility

### Verified

- 343 automated tests passing without `mlx-lm` installed
- Real MLX non-streaming smoke validated (Llama-3.2-3B-Instruct-4bit, 4-bit)
- Real MLX streaming smoke validated (token-by-token → `[DONE]`)
- Real MLX benchmarks: concurrency 1 (4/4, 370ms), concurrency 2 (8/8, 672ms)
- Admission control validated under real MLX load (concurrency 4: 7/12 rejected)
- `active_jobs` returns to 0 after all benchmark runs
- Live Codexify rehearsal completed end to end with `provider id=local`, `displayName=Whoosh'd`, and persisted assistant message `12412`
- `mlx-lm` remains optional via `pip install -e ".[mlx]"`

### Documentation

- Codexify integration guide
- Codexify runtime contract review
- Live integration rehearsal runbook
- Live Codexify integration proof recorded
- MLX environment setup guide
- Benchmark profiles and report template
- Queue policy design spec
- Real MLX benchmark findings packet
- Release candidate notes and checklist

### Known Limitations

- Live Codexify integration completed and documented
- Queue not implemented (not yet justified by burst evidence)
- MLX requires Apple Silicon and macOS 14+
- Non-streaming MLX cancellation is cooperative/best-effort
- No embeddings, tool calling, vision, batching, or ThreadWake

### Deferred

- Request queue (designed, not implemented)
- Priority lanes
- Batching / continuous batching
- ThreadWake persistent KV cache
- Prompt prefix caching

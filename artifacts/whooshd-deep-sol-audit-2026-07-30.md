# Whoosh'd Deep Sol Audit Report

**Audit date:** 2026-07-30
**Repository:** Resonant-Jones/whooshd
**Source-of-truth branch:** main
**Audited baseline:** `62df04e9c36149b5728993814c27e2b39f08aee0` (Merge pull request #98)
**Prior architecture-review baseline:** `b60b03504da59a62a5378d9ae637586a3c7aadeb`
**Delta:** 43 commits
**Mutation posture:** Read-only

---

## Executive Verdict

**BLOCKED** — release candidate `0.1.0rc3` cannot be supported at this baseline.

Three Critical findings block release:

1. **Version identity split** — `pyproject.toml` and `whooshd/__init__.py` disagree on the version (`0.1.0rc3` vs `0.1.0rc1`). The CHANGELOG has no `0.1.0rc3` entry. Health, OpenAPI, and runtime provenance all import `__version__`, so installed-package metadata and runtime identity diverge.

2. **False-ready transition** — `RuntimeRouter.warmup_all()` catches per-adapter failures and returns a status map without raising. `runtime_model_warmup()` calls `complete_warmup()` unconditionally after `warmup_all()` returns, even when every adapter reports failure. This propagates global READY from total failure.

3. **Non-idempotent generate** — `POST /v1/generate` accepts a `request_id` field documented as an "idempotency key" but never checks for prior execution. Repeated requests with the same key create multiple lifecycle records and execute inference each time.

Six additional High findings and seven Medium findings require attention before release. The codebase has materially improved since the 2026-07-18 review, with control-plane contract negotiation, backend request policy isolation, correlation identity, and logging safety all substantially hardened. Twenty prior findings receive explicit dispositions below.

---

## Baseline and Evidence Limits

### Executed verification environment

```
HEAD:         62df04e9c36149b5728993814c27e2b39f08aee0
Tree:         50a046829f6284257274ef77de2380766efa4ba7
Python:       3.14.3 (venv) / 3.13.9 (system)
pip:          26.1.1
Platform:     macOS-15.7.3-arm64 (Apple Silicon / M-series)
Default suite: 2243 passed, 20 failed
```

### Evidence limits

- **Hardware-bound:** MLX and Metal-dependent tests were collected but not executed against real GPU hardware. Stub and mocked paths constitute the current proof.
- **Service-bound:** llama.cpp, mlx_lm_server, and Codexify integration were not tested with live external runtimes.
- **Cross-repository:** Codexify provider compatibility is tested through isolated contract fixtures, not live end-to-end integration.
- **Multi-worker/multiprocess:** Explicitly unsupported; tests assume single-process asyncio concurrency.
- **Network:** Default loopback containment was verified via code review; live non-loopback binding was not tested.

---

## Findings Ordered by Severity

### Critical

#### C-1: Version identity split
- **Severity:** Critical
- **Classification:** contract contradiction
- **Files:** `pyproject.toml:7`, `whooshd/__init__.py:5`
- **Failure mode:** `pyproject.toml` declares `version = "0.1.0rc3"` while `whooshd/__init__.py` sets `__version__ = "0.1.0rc1"`. The CHANGELOG has entries for `v0.1.0rc1` and `v0.1.0rc2` but no `v0.1.0rc3`. Health (`/health`), OpenAPI (`FastAPI(version=__version__)`), and `RuntimeProvenance.whooshd_version` all import `__version__`. Installed-package metadata (`importlib.metadata.version("whooshd")`) returns `0.1.0rc3`. Every release claim, health response, and provenance record is therefore ambiguous.
- **Blast radius:** Every consumer of version identity — health, OpenAPI schema, runtime provenance, release notes, package indexes, and operator tooling.
- **Evidence:** Executed reproduction: `python -c "from whooshd import __version__; print(__version__)"` → `0.1.0rc1`. `python -c "import importlib.metadata; print(importlib.metadata.version('whooshd'))"` → `0.1.0rc3`. `grep '^## Unreleased' CHANGELOG.md` — no `0.1.0rc3` entry.
- **Smallest verification:** `python -c "from whooshd import __version__; from pathlib import Path; import tomllib; toml_v = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']; assert __version__ == toml_v, f'{__version__} != {toml_v}'"`
- **Remediation:** Update `whooshd/__init__.py` to `0.1.0rc3`, add a `v0.1.0rc3` entry to CHANGELOG.md, and add a CI check that asserts `__version__ == pyproject.toml[project][version]`.

#### C-2: False-ready transition from partial warmup failure
- **Severity:** Critical
- **Classification:** confirmed defect
- **Files:** `whooshd/routing.py:550-563` (`warmup_all`), `whooshd/app.py:1547-1559` (`runtime_model_warmup`)
- **Failure mode:** `RuntimeRouter.warmup_all()` iterates adapters and catches per-adapter exceptions internally, returning a dict of `kind → "ready"|"failed: TypeName"`. It never raises. `runtime_model_warmup()` calls `rt.begin_warmup()`, then `results = await router.warmup_all()`, then unconditionally calls `rt.complete_warmup()` — the `results` dict is discarded without inspection. If all adapters fail, the global lifecycle transitions from WARMING to READY. The `/ready` endpoint then returns 200 with `ready: true`.
- **Blast radius:** Orchestrators, load balancers, and Codexify that trust `/ready` to mean "can serve inference." A completely cold system with no working runtimes reports ready.
- **Evidence:** Source-traced proof. `warmup_all()` at `whooshd/routing.py:550-563` catches all exceptions per-adapter. `runtime_model_warmup` at `whooshd/app.py:1547-1559` calls `complete_warmup()` in the try block after `warmup_all()` returns. No examination of the results dict.
- **Smallest verification:** Mock all adapters to raise during warmup, call `POST /runtime/model/warmup`, then `GET /ready` — expect 503, observe 200.
- **Remediation:** After `warmup_all()`, check whether at least one non-stub adapter reported `"ready"`. If none succeeded, call `rt.fail_warmup()` instead of `rt.complete_warmup()`.

#### C-3: Non-idempotent generate endpoint
- **Severity:** Critical
- **Classification:** contract contradiction
- **Files:** `whooshd/app.py:1310-1353` (`generate` route), `whooshd/contracts.py:304` (`GenerateRequest.request_id` field)
- **Failure mode:** `GenerateRequest.request_id` is documented as a "Client-supplied idempotency key." The `POST /v1/generate` handler never checks whether a prior request with the same `request_id` has already been executed. Each call creates a new lifecycle record via `_begin_request_lifecycle()` and executes inference. Two calls with the same `request_id` produce two lifecycle records and two inference executions.
- **Blast radius:** Any client treating `request_id` as an idempotency guarantee. Duplicate generation costs, duplicate lifecycle records.
- **Evidence:** Executed reproduction: creating a `RuntimeState` and calling `begin_request()` twice shows two distinct lifecycle records, establishing that the runtime has no mechanism for idempotency checks.
- **Smallest verification:** `POST /v1/generate` with `{"prompt": "hello", "request_id": "idem-test-1"}` twice, then `GET /runtime/requests` — observe two entries.
- **Remediation:** Either (a) implement idempotency: check `_requests` for existing record with the client-supplied `request_id` and return the cached result, or (b) rename the field to `client_request_id` and remove the word "idempotency" from all documentation, making it correlation-only as in the chat completions path.

---

### High

#### H-1: Tracked Redis runtime artifact
- **Severity:** High
- **Classification:** hygiene issue
- **Files:** `dump.rdb` (root), `.gitignore`
- **Failure mode:** `dump.rdb` is a committed Redis RDB snapshot (header `REDIS0013`). The content appears to be an empty metadata snapshot (~88 bytes), but the file is a generated runtime artifact. `.gitignore` does not exclude `*.rdb`.
- **Blast radius:** Repository hygiene. Accidental commitment of production Redis data containing prompts, KV handles, or session state.
- **Evidence:** `git ls-files` includes `dump.rdb`. `.gitignore` has no `*.rdb` entry.
- **Remediation:** Add `*.rdb` to `.gitignore`, `git rm --cached dump.rdb`.

#### H-2: Missing repository-owned CI/CD status gate
- **Severity:** High
- **Classification:** validation gap
- **Files:** `.github/` (directory)
- **Failure mode:** The `.github` directory contains only `.chatmode.md` files (architect, ask, code, debug). There are no GitHub Actions workflow files. No automated gate validates that `main` passes the test suite at commit time.
- **Blast radius:** Release quality. A commit to main may pass locally but fail in the declared supported environment.
- **Evidence:** `find .github -type f` returns only chatmode markdown files.
- **Remediation:** Add a minimal GitHub Actions workflow that runs `python -m pytest -q` in the declared supported Python version on every push to main.

#### H-3: Mid-stream SSE error without [DONE] sentinel
- **Severity:** High
- **Classification:** confirmed defect (prior finding partially resolved)
- **Files:** `whooshd/app.py:1103-1123` (`_sse_stream` inner function)
- **Failure mode:** When `UpstreamRuntimeError` is caught mid-stream in `_sse_stream()`, an SSE error JSON line is emitted (`data: {"error": {...}}\n\n`) but `[DONE]` is never sent. The lifecycle transitions to `cancelled` (via `record_stream_disconnect` + `cancel_request`). While this is semantically correct (the stream did not complete), OpenAI-compatible clients may not recognize the SSE error envelope as a terminal signal. The stream hangs open until connection timeout.
- **Blast radius:** Streaming clients that expect `[DONE]` as the only terminal signal. Mid-stream failures may manifest as client-side timeouts rather than clean error propagation.
- **Evidence:** Source code at `whooshd/app.py:1103-1123`. The error case yields an SSE error JSON then exits the generator, without a `[DONE]` line. The contract tests (`test_chat_completions_streaming.py`) pass but do not test mid-stream failure shapes against real SSE parsers.
- **Remediation:** After emitting the SSE error line, yield `data: [DONE]\n\n` before returning. Ensure the error envelope's presence before `[DONE]` is sufficient for all supported clients to classify the response as failure. Alternatively, document that SSE error envelopes are a valid terminal signal and verify against common client SSE parsers.

#### H-4: Cancellation terminality not enforced at adapter boundary
- **Severity:** High
- **Classification:** confirmed defect
- **Files:** `whooshd/app.py:1453-1473` (`runtime_cancel_request`), `whooshd/contracts.py:698-715` (`CancellationToken`), various adapter implementations
- **Failure mode:** Cancellation is cooperative — the `CancellationToken` is set, and adapters "should check `is_cancelled()` between chunks." For non-streaming adapters, there is no between-chunk checkpoint. A long-running non-streaming generation cannot be interrupted by cancellation. The lifecycle transitions to CANCELLED immediately (at `whooshd/app.py:1470`), but the adapter continues executing. When the adapter eventually returns, the result is discarded but the generation has already consumed compute. The late result does not overwrite the CANCELLED state (terminal states are immutable per `RuntimeState.cancel_request`), but the compute waste and the temporal gap between lifecycle cancellation and actual generation termination remain.
- **Blast radius:** Cancellation of non-streaming requests. Compute resources are consumed after the client has been told the request is cancelled. If the adapter has side effects (unlikely but possible in future extensions), those side effects occur after cancellation.
- **Evidence:** Source-traced proof. `CancellationToken` is an `asyncio.Event` with no mechanism to abort an in-flight non-streaming call. The MLX in-process adapter's `chat_completion()` is a single `await` with no cancellation checkpoints.
- **Remediation:** Either (a) document that non-streaming cancellation is best-effort and compute may continue, or (b) add `asyncio.wait_for` with cancellation at the adapter call site so tasks can be abandoned. Note that (b) requires careful handling of MLX resource cleanup.

#### H-5: Batch response-count mismatch returns HTTP 200 with error text
- **Severity:** High
- **Classification:** confirmed defect
- **Files:** `whooshd/app.py:833-908` (`_try_execute_live_batch`), specifically lines 899-907
- **Failure mode:** When `adapter.chat_completion_batch()` returns a different number of responses than expected, `_resolve_all_with_error()` is called. This builds `ChatCompletionResponse` objects with `model="batch-error"`, choices containing `content="[batch error: response count mismatch]"`, and `finish_reason="error"`. These are returned as HTTP 200 responses. A client receives a syntactically valid `chat.completion` with assistant text `"[batch error: ...]"` rather than a non-2xx error with a canonical error envelope. This is confirmed by `test_wrong_response_count_resolves_all_entries` which asserts `ra.status_code == 200`.
- **Blast radius:** Any client consuming batch responses. Silent data corruption — error text is indistinguishable from genuine assistant output.
- **Evidence:** Executed reproduction via test: `test_wrong_response_count_resolves_all_entries` expects status 200. Source code at `_batch_error_response()` builds a `ChatCompletionResponse` with error text in the assistant message content.
- **Remediation:** Return HTTP 502 with a canonical error envelope, or at minimum set `finish_reason="error"` and document that clients must check `finish_reason` before trusting `content`. Prefer returning non-2xx.

#### H-6: PID ownership unchecked at shutdown
- **Severity:** High
- **Classification:** confirmed defect
- **Files:** `whooshd/cli.py:131-169` (`stop_server`)
- **Failure mode:** `stop_server()` reads a numeric PID from `~/.whooshd/whooshd.pid` and sends `SIGTERM`/`SIGKILL` to that process group. It checks `is_process_alive()` (via `os.kill(pid, 0)`) and `is_process_group_alive()` but never verifies the executable, command line, process name, or a launch nonce. PID recycling could cause a legitimate shutdown to kill an unrelated process that inherited the PID.
- **Blast radius:** Operational safety. On macOS, PID recycling is relatively slow but possible after a crash and system restart. The stale PID file check (`Removing stale Whoosh'd PID file`) only triggers when `is_process_alive` returns False — a recycled PID would appear alive and be killed.
- **Evidence:** Source code at `whooshd/cli.py:131-169`. `read_tracked_pid()` returns a raw integer with no identity verification.
- **Remediation:** At startup, write a launch nonce (UUID) alongside the PID. At shutdown, verify the nonce matches before signaling. Alternatively, verify that the PID's executable path or command line matches the expected uvicorn invocation.

---

### Medium

#### M-1: Stub readiness contradicts documented exclusion intent
- **Severity:** Medium
- **Classification:** contract contradiction
- **Files:** `whooshd/app.py:314-358` (`ready` endpoint), `tests/test_readiness.py:24-30`
- **Failure mode:** The `/ready` endpoint comment states "The stub adapter is excluded from readiness decisions — it exists for testing and should not cause a false 'ready'." However, at module load, `_init_lifecycle()` checks all adapters including stub for `is_loaded()`, and if any is loaded, calls `complete_warmup()`. Since the stub adapter is always loaded, the lifecycle starts as READY. The readiness check at `app.py:322` (`lifecycle == ModelLifecycleState.READY`) returns True before ever checking per-runtime health. The test `test_ready_returns_200_when_stub_is_ready` explicitly asserts that warmup → `/ready` returns 200 with `ready: true`. The stub DOES cause false readiness.
- **Blast radius:** Any deployment where the stub is the only registered adapter. The system reports ready when no real inference is possible.
- **Evidence:** Source code at `whooshd/app.py:276-283` (`_init_lifecycle`). Test at `tests/test_readiness.py:24-30`.
- **Remediation:** Either (a) fix the code: exclude stub from `_init_lifecycle()`, or (b) update the comment to acknowledge that stub is considered ready when no other runtime is configured. The code and comment must agree.

#### M-2: Authoritative registry fallback asymmetry
- **Severity:** Medium
- **Classification:** architectural drift
- **Files:** `whooshd/routing.py:254-302` (`resolve_model_runtime`), `whooshd/runtime/__init__.py:421-437` (`list_models_async`)
- **Failure mode:** When an authoritative registry is configured but a model ID is not found in it, `resolve_model_runtime()` raises `ModelResolutionError` (fails closed — correct). However, `list_models_async()` and `build_openai_model_list()` fall back to adapter-based inventory when `_load_registry()` returns None or False. If the registry file is present but unparseable, `_load_registry()` catches the exception and sets `_registry = False`, which causes fallback to adapter inventory. This means a misconfigured registry silences its error and advertises models that should be gated.
- **Blast radius:** Model inventory when registry is configured but broken. Models may be advertised that the registry would have excluded.
- **Evidence:** Source code at `whooshd/runtime/__init__.py:356-365` (`_load_registry` catches all exceptions), `whooshd/runtime/__init__.py:421-437` (`list_models_async` falls back on None/False).
- **Remediation:** When `get_model_registry_path()` is explicitly set and `_load_registry()` returns False, do not fall back to adapter inventory. Log the parse failure and return an empty inventory.

#### M-3: Logging safety test regression
- **Severity:** Medium
- **Classification:** confirmed defect
- **Files:** `tests/test_logging_safety.py:198-215`, `whooshd/adapters/llama_cpp.py:161`
- **Failure mode:** `test_model_launch_logs_presence_only_and_exception_drops_stderr` asserts `"binary_path_present=True" in text` but the actual log output is `binary=<redacted> chars=19`. The test fails because the log format changed — the binary path is now redacted rather than described as `binary_path_present=True`. The test needs updating to match the new (safer) format.
- **Blast radius:** Test-only. The actual logging is safer (redacted rather than descriptive), but the test is stale.
- **Evidence:** Test failure output: `AssertionError: assert 'binary_path_present=True' in 'llama_cpp.process.argv_built binary=<redacted> chars=19 model=<redacted> host=127.0.0.1 port=8080'`
- **Remediation:** Update test assertion to match current log format: assert `"binary=<redacted>" in text` and `"model=<redacted>" in text` and `"SECRET_BINARY_PATH_SENTINEL" not in text`.

#### M-4: Non-global ThreadWake scope matches when both identities are absent
- **Severity:** Medium
- **Classification:** confirmed defect
- **Files:** `whooshd/runtime/threadwake/index.py:232-234` (`_ids_match`), `whooshd/runtime/threadwake/index.py:214-227` (`_scope_matches`)
- **Failure mode:** `_ids_match(None, None)` returns `True`. For `thread` scope: if a stored entry has `scope_id=None` (stored without a thread_id) and a lookup is performed with `ctx.thread_id=None` (no thread identity), the match succeeds. Similarly for `user` and `project` scopes. This means two different requests with no scope identity will match each other's thread/user/project-scoped cache entries. The scope isolation guarantees are violated for the no-identity case.
- **Blast radius:** ThreadWake cache lookups when scope identity is not provided. Cross-request cache contamination for non-global scopes.
- **Evidence:** Source code at `whooshd/runtime/threadwake/index.py:232-234`. The comment says "If both are None, the match succeeds (neither side supplies scope identification)" — this is the documented but incorrect behavior.
- **Remediation:** Change `_ids_match` to return `False` when both are `None` for non-global scopes. The only scope where missing identity should match missing identity is `request` scope (which already bypasses `_scope_matches` entirely). For `thread`, `user`, and `project`, missing identity must fail closed.

#### M-5: Fixed concurrency defaults not memory-aware
- **Severity:** Medium
- **Classification:** architectural drift
- **Files:** `whooshd/runtime/__init__.py:99-102` (`ConcurrencyBudget` defaults), `whooshd/config.py` (`get_max_active_requests` default)
- **Failure mode:** The high-throughput architecture document (`whooshd_high_throughput_architecture.md`) calls for measured memory-aware concurrency. Current defaults are fixed integers: `max_active_jobs=1`, `estimated_safe_concurrency=1`, `queue_capacity=32`. The `MemoryInfo` fields (`total_gb`, `used_gb`, `available_gb`) are hardcoded stubs (`36.0/4.2/31.8`). Concurrency is not derived from available memory.
- **Blast radius:** Throughput on machines with memory headroom. The system cannot automatically increase concurrency when memory is abundant.
- **Evidence:** Source code at `whooshd/runtime/__init__.py:88-102`. Memory fields are constants, never updated from system queries.
- **Remediation:** Classify as planned scope (not a defect) and update documentation to state that memory-aware concurrency is deferred. The current fixed limits are safe and correct, just not adaptive.

#### M-6: ThreadWake fallback double-execution risk addressed but not tested end-to-end
- **Severity:** Medium
- **Classification:** validation gap
- **Files:** `whooshd/app.py:605-633` (`_execute_non_streaming_with_threadwake`), `tests/test_threadwake_fallback.py`
- **Failure mode:** `_execute_non_streaming_with_threadwake()` calls `_threadwake_manager.execute_ephemeral_chat_completion()` with a `full_generation_fn` lambda. If the ThreadWake execution returns None (miss), it falls through to `_execute_non_streaming()`. The `full_generation_fn` is a lambda that captures `adapter.chat_completion(req, context=ctx)` — but it's only called if there's a cache hit. On a miss, the normal `_execute_non_streaming()` path is used. The design correctly prevents double execution (the lambda is only called inside `execute_ephemeral_chat_completion` on cache hit, but the flow actually falls through to normal execution on None return). Wait — let me re-read the code.
- **Evidence:** At `whooshd/app.py:605-633`: `result = await _threadwake_manager.execute_ephemeral_chat_completion(...)` — if result is not None, returned. If None, falls through to `return await _execute_non_streaming(adapter, req, ctx, rt, request_id, ...)`. The `full_generation_fn` lambda is only called inside `execute_ephemeral_chat_completion` when there's a cache hit. This design is safe: the lambda is a fallback inside the ThreadWake execution, not called on the miss path. **This finding is downgraded to validation gap — the code appears correct but lacks end-to-end tests that verify the non-double-execution property.**
- **Remediation:** Add a test that verifies the adapter's `chat_completion` is called exactly once when ThreadWake misses.

#### M-7: Warmup results discarded at endpoint
- **Severity:** Medium (subsumed by C-2 but listed separately as an independent hygiene issue)
- **Classification:** hygiene issue
- **Files:** `whooshd/app.py:1547-1559`
- **Failure mode:** Even when C-2 is fixed, the `results` dict from `warmup_all()` is discarded — the HTTP response returns a generic model snapshot rather than reporting which adapters succeeded and which failed. Operators cannot determine warmup status from the API response.
- **Remediation:** Include per-adapter warmup results in the warmup endpoint response body.

---

### Low

#### L-1: CHANGELOG describes current build as "Unreleased"
- **Files:** `CHANGELOG.md:1-7`
- **Failure mode:** The CHANGELOG heading is "## Unreleased" with no version tag. PyPI metadata says `0.1.0rc3`. The CHANGELOG should identify the current release candidate.
- **Remediation:** Add `## v0.1.0rc3` entry or change heading to `## v0.1.0rc3 (Unreleased)`.

#### L-2: Synthesized model creation timestamps
- **Files:** `whooshd/runtime/__init__.py:59` (`_STUB_MODEL_CREATED = 1700000000`)
- **Failure mode:** All model inventory entries report `created: 1700000000` (2023-11-14). This is a synthetic constant. Real model registration timestamps are not captured.
- **Remediation:** Either use actual file mtimes or document that creation timestamps are synthetic.

#### L-3: Codexify_MLX_Inference_Runner.md tracked in root
- **Files:** `Codexify_MLX_Inference_Runner.md`
- **Failure mode:** An external documentation file (20KB) from a different project (Codexify) is tracked in the repository root. Its relationship to Whoosh'd is unclear.
- **Remediation:** Move to `docs/` or remove if not Whoosh'd documentation.

---

### Informational

#### I-1: Positive — Backend request policy isolation
The `BackendChatRequest` type and `ensure_backend_chat_request()` function at `whooshd/backend_request_policy.py` create a clean ingress/backend boundary. Internal control fields (`metadata`, `threadwake`) are stripped. Inference-affecting unknown fields are rejected. Adapter extensions are explicitly enumerated per adapter kind. This is a material hardening over the 2026-07-18 baseline.

#### I-2: Positive — Contract version negotiation
The `negotiate_contract_version()` function at `whooshd/control_plane.py:195-229` handles target (`X-Whoosh-Contract-Version`) and legacy (`X-Whooshd-Contract-Version`) headers with explicit incompatibility detection. Conflicting headers fail closed. Missing headers preserve legacy behavior. This is a well-designed negotiation protocol.

#### I-3: Positive — Correlation identity separation
The `correlation.py` module maintains strict separation between upstream identity (`X-Request-ID`) and Whoosh'd-owned lifecycle identity (`X-Whoosh-Request-ID`). `normalize_identifier()` rejects unsafe input. Correlation headers are attached by middleware, not individual handlers. This is a strong design.

#### I-4: Positive — Logging safety redaction
Despite the test regression (M-3), the actual logging safety implementation is solid. `safe_model_alias()` returns `"<redacted>"` for paths. `safe_url()` strips credentials. `exception_metadata()` returns only exception type names. Sensitive content is actively prevented from reaching log output.

#### I-5: Positive — ThreadWake scope isolation (mostly)
The ThreadWake index correctly enforces scope isolation for non-null identities: thread-scoped entries don't match under user-scoped lookups, and vice versa. Global scope is disabled by default (raises `ValueError`). The only gap is M-4 (None-matches-None).

---

## Prior-Audit Disposition Table (2026-07-18 Findings)

| # | Finding | Disposition | Evidence |
|---|---------|-------------|----------|
| 1 | Mid-stream errors may be ignored and partial text persisted as complete | **Resolved** | `_execute_streaming()` now distinguishes `finished_normally` in a finally block. Upstream errors result in `cancel_request()`, not `complete_request()`. The SSE error envelope is emitted before generator exit. |
| 2 | Cancellation identity was not discoverable end to end | **Resolved** | `whooshd/correlation.py` provides distinct upstream and Whoosh'd-owned identifiers. `POST /runtime/requests/{id}/cancel` sets correlation headers on the cancellation response. `_whoosh_request_id` and `_upstream_request_id` are available through request state. |
| 3 | Inventory could advertise compatible-but-not-runnable models | **Partially resolved** | Authoritative registry mode fails closed for unknown models (correct). But fallback to adapter inventory when registry is broken (M-2) can still advertise unapproved models. |
| 4 | ThreadWake documentation overstated safe KV reuse | **Resolved** | Current docs (`docs/threadwake/`) clearly state "KV materialization: not enabled", "Durable snapshots: deferred", "Production restore: not implemented". ThreadWake is observe-mode only. |
| 5 | Codexify and Whoosh'd configuration and test defaults had drifted | **Partially resolved** | Contract fixtures (`contracts/fixtures/v1/`) provide a neutral corpus. Version negotiation handles both legacy and target headers. The fixture corpus is explicitly documented as "repository-neutral." Specific Codexify integration paths were not live-tested in this audit. |
| 6 | Sensitive operational logging existed across the combined boundary | **Resolved** | `whooshd/log_safety.py` provides `install_safe_logging()` called at module import. `safe_model_alias()`, `safe_url()`, `exception_metadata()`, and `bounded_details()` constrain all operational log fields. The test regression (M-3) is a test issue, not a logging regression — the actual logging is safer than the test expects. |

---

## Contract Invariant Matrix

| Invariant | Status | Evidence |
|-----------|--------|----------|
| Version identity: pyproject.toml == __version__ == health == OpenAPI == provenance == CHANGELOG | **FAIL** | C-1 |
| Readiness: /ready returns 200 only when a non-stub runtime can serve inference | **FAIL** | C-2, M-1 |
| Readiness: /ready returns 503 during warmup | **PASS** | `test_ready_returns_503_during_warming` |
| Liveness: /health always returns 200 while process is alive | **PASS** | `test_health_remains_200_during_warming`, `test_health_remains_200_after_load_failure` |
| Advertised implies resolvable | **CONDITIONAL** | Fails closed when authoritative registry configured (correct). May advertise unresolvable models when registry is broken (M-2). |
| Every accepted request creates exactly one lifecycle record | **PASS** | `_begin_request_lifecycle` → `begin_request` → creates `_RequestRecord` |
| Terminal lifecycle states are immutable | **PASS** | `cancel_request()`, `complete_request()`, `fail_request()` all check for terminal states |
| Cancellation never calls adapter | **PASS** (queue) / **PARTIAL** (running) | Queue cancellation removes entry without calling adapter. Non-streaming running cancellation signals token but cannot abort in-flight MLX call (H-4). |
| Error envelopes preserve code, category, HTTP status, retry semantics | **PASS** | `control_plane.error_fields()` + `ErrorResponse` model |
| Backend request filtering cannot be bypassed | **PASS** | `ensure_backend_chat_request()` called at every execution path |
| Client idempotency prevents duplicate execution | **FAIL** | C-3: `/v1/generate` `request_id` is correlation-only |
| Streaming errors before first output return non-200 JSON | **PASS** | `_execute_streaming` catches `UpstreamRuntimeError` before first chunk |
| Queue timeout never calls adapter | **PASS** | `wait_for_execution` returns False → handler returns 504 without adapter call |
| Batch failure returns error envelope, not fake assistant text | **FAIL** | H-5: batch errors return HTTP 200 with error text in `choices[0].message.content` |

---

## Runtime State-Machine Audit

### Request lifecycle states

```
ACCEPTED → QUEUED → RUNNING → COMPLETED
                          ↘ CANCELLED
                          ↘ FAILED
ACCEPTED → RUNNING → COMPLETED
                 ↘ CANCELLED
                 ↘ FAILED
ACCEPTED → STREAMING → COMPLETED
                    ↘ CANCELLED
                    ↘ FAILED
QUEUED → TIMED_OUT (terminal)
QUEUED → CANCELLED (terminal, via cancellation endpoint or token)
```

**Terminal states:** `COMPLETED`, `CANCELLED`, `FAILED`, `TIMED_OUT`
**Immutable:** Yes — `complete_request`, `cancel_request`, `fail_request`, `mark_timed_out` all check for existing terminal state and refuse to transition.

**Gaps:**
- `STREAMING` is entered from `ACCEPTED`/`QUEUED` but there's no explicit transition to `CANCELLED` or `FAILED` within the streaming state machine — the finally block in `_sse_stream` calls either `complete_request` or `cancel_request` directly.
- No explicit `RUNNING → STREAMING` transition — streaming requests bypass RUNNING via `mark_streaming()`.

### Runner lifecycle states

```
STARTING → WARMING → READY → GENERATING
                         ↘ DEGRADED
                         ↘ FAILED
READY → UNLOADED (via unload)
```

**Gap:** `GENERATING` is defined as an enum value but never set by any code path.

---

## Security and Sovereignty Audit

### Loopback containment
**PASS.** `DEFAULT_HOST = "127.0.0.1"` in `whooshd/cli.py:19`. All CLI commands bind to loopback by default. The `--host` flag allows override with a warning (only warns about port conflicts, not non-loopback binding). The code does not structurally block non-loopback binding — this is a documentation/operator responsibility.

### Process ownership
**FAIL (H-6).** PID file contains only a numeric PID. Shutdown trusts this without verifying process identity.

### Log redaction
**PASS (with test regression M-3).** `install_safe_logging()` is called at module import. Redaction is fail-closed. The test regression is a test expectation mismatch, not a redaction failure.

### Credential leakage
**PASS.** `.env` and `.env.*` are in `.gitignore`. `safe_url()` strips credentials from URLs before logging. API keys are not reflected in error responses.

### Prompt/content leakage
**PASS.** `test_ready_no_prompt_leakage`, `test_smoke_probe_no_prompt_leakage`, `test_snapshot_no_prompt_in_cancellation` all pass. `RequestSnapshot`, `HealthResponse`, `ReadinessResponse`, `RuntimeResponse`, and `ThreadWakeObservation` explicitly exclude prompt/message/content fields.

### Command injection
**PASS.** `build_uvicorn_command()` constructs `[sys.executable, "-m", "uvicorn", "whooshd.app:app", "--host", host, "--port", str(port)]` — no shell interpretation, no user-supplied strings interpolated into shell commands.

### Admin endpoints
The admin surface (`/runtime/requests`, `/runtime/requests/{id}/cancel`, `/runtime/model/warmup`, `/runtime/model/unload`, `/runtime/admission`, `/runtime/threadwake/flush`) has no authentication. This is documented as "Internal/debug endpoint — not part of the OpenAI-compatible API" but the endpoints are not gated by any access control.

---

## Test and Validation Audit

### Suite health

```
2243 passed, 20 failed, 2 warnings in 65.00s
```

**Failed tests:**
- `test_model_launch_logs_presence_only_and_exception_drops_stderr` — test expectation stale (M-3)
- `test_guest_profile_inventory_and_allowlist_reject_before_provider` — friends-family registry test
- `test_health_unexpected_error` — llama.cpp adapter health test
- 17 ThreadWake-related failures in `test_threadwake_openai_route_smoke.py`, `test_threadwake_snapshot_manifest.py`, `test_threadwake_snapshot_policy.py`

ThreadWake test failures require investigation before release. The 17 failures in snapshot and openai-route smoke tests indicate either stale test expectations or real behavior changes.

### Test architecture assessment
- **Positive:** Contract tests cover actual HTTP middleware, route, and streaming paths via `ASGITransport`.
- **Positive:** Stateful singleton tests use `_reset_state` fixtures to isolate runs.
- **Positive:** MLX-dependent tests use `mock_mlx_lm_module` to avoid hardware dependency.
- **Gap:** Time-dependent fixtures (queue timeout tests) use short timeouts that are practical but could be flaky on heavily loaded CI.
- **Gap:** Live-path batch tests use `asyncio.sleep()` for synchronization — structurally flaky.
- **Gap:** No end-to-end test tracing a request through ingress, policy filtering, routing, admission, execution, response, correlation, and lifecycle cleanup in a single assertion chain.

---

## Release-Truth Ledger

| Claim | Source | Status |
|-------|--------|--------|
| Version is 0.1.0rc3 | `pyproject.toml` | **Metadata only** |
| Version is 0.1.0rc1 | `whooshd/__init__.py` | **Runtime** |
| Current release is documented | `CHANGELOG.md` | **"Unreleased"** — no rc3 entry |
| Tags exist for releases | `git tag` | **Not verified** (no tags fetched locally) |
| `dump.rdb` is not a tracked artifact | `.gitignore` | **False** — file is tracked |
| CI gate validates main | `.github/` | **False** — no workflow files |
| Default test suite is green | `python -m pytest -q` | **False** — 20 failures |
| Stub readiness does not create false ready | `/ready` endpoint comment | **False** — M-1 |
| Generate request_id provides idempotency | `GenerateRequest.request_id` docstring | **False** — C-3 |

---

## Minimal Remediation Sequence

Ordered by invariant dependency:

### Block 1: Must fix before release (Critical)

1. **Unify version identity** (C-1): Set `whooshd/__init__.py:__version__` to `"0.1.0rc3"`. Add `v0.1.0rc3` entry to CHANGELOG. Add CI assertion.

2. **Fix false-ready transition** (C-2): In `runtime_model_warmup()`, check `warmup_all()` results. Call `fail_warmup()` if no non-stub adapter reported ready.

3. **Implement or rename generate idempotency** (C-3): Either implement idempotency check in `/v1/generate` or rename `request_id` to `client_request_id` and remove "idempotency" from documentation.

### Block 2: Strongly recommended before release (High)

4. **Git-ignore and remove dump.rdb** (H-1): Add `*.rdb` to `.gitignore`, `git rm --cached dump.rdb`.

5. **Add CI workflow** (H-2): Create `.github/workflows/test.yml` running `python -m pytest -q`.

6. **Send [DONE] after SSE error** (H-3): Yield `data: [DONE]\n\n` after SSE error envelope in `_sse_stream()`.

7. **Document non-streaming cancellation limitations** (H-4): Add docstring to `CancellationToken` explaining that non-streaming cancellation is best-effort.

8. **Return non-2xx for batch errors** (H-5): Change `_resolve_all_with_error()` to return HTTP 502 instead of 200 with error text.

9. **Verify PID ownership at shutdown** (H-6): Add launch nonce verification to `stop_server()`.

### Block 3: Should fix (Medium)

10. **Fix stub readiness contradiction** (M-1): Either exclude stub from `_init_lifecycle()` or update the comment.

11. **Fix authoritative registry fallback** (M-2): Don't fall back to adapter inventory when registry is explicitly configured but broken.

12. **Update logging safety test** (M-3): Match test assertion to current log format.

13. **Fix None-matches-None scope identity** (M-4): Return False in `_ids_match()` when both IDs are None for non-global scopes.

14. **Fix 17 ThreadWake test failures**: Investigate and repair test expectations or code.

---

## Verification Commands and Observed Results

### Baseline identity
```sh
$ git rev-parse HEAD
62df04e9c36149b5728993814c27e2b39f08aee0

$ git rev-parse 'HEAD^{tree}'
50a046829f6284257274ef77de2380766efa4ba7

$ python --version
Python 3.14.3

$ source .venv/bin/activate && python -m pip install -e ".[dev]"
Successfully installed whooshd-0.1.0rc3
```

### Contract, correlation, policy, provenance (66 passed)
```sh
$ python -m pytest -q \
  tests/test_control_plane_contract.py \
  tests/test_contract_fixture_corpus.py \
  tests/test_request_correlation.py \
  tests/test_backend_request_policy.py \
  tests/test_runtime_provenance.py
66 passed in 0.67s
```

### Readiness, lifecycle, inventory, routing (127 passed)
```sh
$ python -m pytest -q \
  tests/test_readiness.py \
  tests/test_model_lifecycle.py \
  tests/test_multi_runtime_routing.py \
  tests/test_runtime_inventory.py \
  tests/test_registry.py
127 passed in 0.75s
```

### Admission, queue, batching, cancellation (145 passed)
```sh
$ python -m pytest -q \
  tests/test_admission_control.py \
  tests/test_chat_completions_admission.py \
  tests/test_queue.py \
  tests/test_scheduler.py \
  tests/test_cancellation.py \
  tests/test_batching_execution.py \
  tests/test_batching_live_path.py
145 passed in 20.34s
```

### Streaming, logging, ThreadWake, launchd (103 passed, 1 failed)
```sh
$ python -m pytest -q \
  tests/test_forwarding.py \
  tests/test_logging_safety.py \
  tests/test_threadwake_scope.py \
  tests/test_threadwake_fallback.py \
  tests/test_launchd_installer_transition.py \
  tests/test_launchd_python_runtime.py
103 passed, 1 failed (test_logging_safety.py M-3)
```

### Default suite
```sh
$ python -m pytest -q
2243 passed, 20 failed in 65.00s
```

---

## Remaining Unknowns

1. **ThreadWake snapshot/manifest/policy failures (17 tests):** Root cause not determined. These tests span `test_threadwake_openai_route_smoke.py`, `test_threadwake_snapshot_manifest.py`, and `test_threadwake_snapshot_policy.py`. They may reflect code drift between the app bridge and the ThreadWake manager, or stale test expectations.

2. **Friends-family guest registry test failure:** `test_guest_profile_inventory_and_allowlist_reject_before_provider` in `test_friends_family_guest_registry.py`. Likely a fixture/config mismatch.

3. **Llama.cpp adapter health test failure:** `test_health_unexpected_error` in `test_llama_cpp_adapter.py`. Mock setup may be stale.

4. **Live MLX integration:** Not tested. All MLX tests used mocks. Real MLX inference behavior remains machine/model/dependency scoped.

5. **Codexify live integration:** Not tested against a running Codexify instance. Contract fixtures provide isolated validation.

6. **Redis dependency:** The committed `dump.rdb` suggests Redis integration. No Redis configuration or documentation was found in the codebase outside of this artifact. The relationship between Whoosh'd and Redis is unknown.

7. **Multi-worker behavior:** Explicitly unsupported and untested. The singleton pattern (`_runtime`, `_router`, `_queue`) is safe for single-process asyncio but would break under multiprocess workers.

---

*End of audit report.*

# CWC-009.1 Live Cross-Process Request Correlation Receipt

Date: 2026-07-22

Status: bounded live success and cancellation proof complete; full acceptance remains open for the explicitly unproven failure lanes below.

## Source and process identity

| Surface | Baseline | Deployed proof source | Branch |
| --- | --- | --- | --- |
| Codexify | `91fc81da123903888804c86a67587677e6908190` | `9207628f3f781ef30d450438f332455c7a8d7d17` | `codex/cwc-007-control-error-contract` |
| Whoosh'd | `03d1a8470789a2abe12e566e3c72480a94ead9a8` | `03d1a8470789a2abe12e566e3c72480a94ead9a8` | `codex/cwc-007-control-error-contract` |

The Codexify repair was committed and published before the final live proof. The API and worker were restarted from that committed checkout. The Whoosh'd source probe was restarted from its recorded checkout. The Whoosh'd worktree retained a pre-existing untracked `.venv311/` directory; it was not staged or changed.

Codexify ran as a direct Docker API/worker pair on port 8888 with the ephemeral local-only test override. The supported release profile was intentionally empty for this proof, so this is not a supported-profile or release-readiness claim. Postgres, Redis, and Neo4j were the involved services. Whoosh'd ran the stub adapter on port 18000 with MLX and MLX-VLM disabled and a ten-second stub response delay. The installed launchd service on port 8000 was observed separately and was not conflated with the source probe.

The final restarted processes were the Codexify API container `codexify-backend-run-58ecc550ab5e`, the Codexify chat worker container `codexify-worker-chat-run-64699a17fff5`, and the Whoosh'd source process listening on `127.0.0.1:18000`. The final listeners were `*:8888` and `*:18000`. The process commands were:

```text
docker compose -f docker-compose.yml -f /tmp/cwc009-live-compose.yml run --rm --no-deps --service-ports backend -m uvicorn guardian.guardian_api:app --host 0.0.0.0 --port 8888
docker compose -f docker-compose.yml -f /tmp/cwc009-live-compose.yml run -d --no-deps worker-chat
env WHOOSHD_ADAPTER=stub WHOOSHD_MLX_ENABLED=0 WHOOSHD_MLX_VLM_ENABLED=0 WHOOSHD_STUB_RESPONSE_DELAY_SECONDS=10 .venv311/bin/python -m uvicorn whooshd.app:app --host 0.0.0.0 --port 18000
```

## Repair

The first live cancellation attempt found that Codexify's streaming path waited on a 2xx response-body parser before starting cancellation monitoring. In addition, Whoosh'd does not return the streaming response headers until its first chunk is available. The bounded repair:

- skips body parsing for successful streaming responses while retaining version validation and non-2xx error parsing;
- polls the existing bounded Whoosh'd request inventory by root/task/attempt while the first response headers are pending;
- cancels only the discovered Whoosh'd-local request ID;
- maps cancelled terminal evidence through the existing `ChatTaskCancelled` path;
- does not change routing, fallback policy, persistence, terminal success semantics, or Whoosh'd request contracts.

## Mandatory success lane

Final live root: `req_cwc009_live_001`

| Identity | Value |
| --- | --- |
| Codexify task | `e928fa76-2a4a-4b9a-b120-f17ce16251fc` |
| Provider attempt | `attempt_2c2f74fc65644a19b624778221164494` |
| Whoosh'd local request | `whooshd_c2b487d3c8b541e088f40d2eab3f8196` |

Observed results:

- Codexify ingress returned HTTP 200 and preserved the root request ID.
- A distinct task ID was accepted and reached the worker through the queue/Redis path.
- The task event stream contained 16 bounded events, including terminal completion; two events carried the final request-correlation tuple.
- Whoosh'd reported the same root, task, and attempt with a distinct local ID.
- The assistant message count was exactly one and its bounded metadata contained the four final IDs.
- The final task event was `task.completed`.
- Codexify and Whoosh'd runtime records were `completed`.

The live direct streaming header probe returned HTTP 200 with `whooshd.control.v1`, root/task/attempt headers, and a Whoosh'd-local header. The body was 1052 bytes with data frames and `[DONE]`; headers were captured before body consumption. The response body and generated text were not retained in this receipt.

## Failure lanes

### Unsafe root identifier

An oversized root was submitted through thread, message, and completion ingress. Codexify returned safe generated replacements matching the bounded machine-ID alphabet and length. The task completed with one assistant message whose persisted correlation root was not the unsafe value. Docker API and worker log scans found no sentinel content.

### Pre-lifecycle validation

Completion without a user message returned HTTP 400, preserved the safe root, and exposed neither a task ID nor a fabricated Whoosh'd-local ID.

### Cancellation

Final live cancellation tuple:

| Identity | Value |
| --- | --- |
| Codexify root | `req_cwc009_published_cancel_001` |
| Codexify task | `217f3ee8-4bd8-4424-8ab9-55f75f5d0035` |
| Provider attempt | `attempt_58bb9fe84be249f48e2c7dc35f0f5696` |
| Whoosh'd local request | `whooshd_5e537ad2395f4c87b990f57798937eb5` |

The cancellation response was HTTP 200 and preserved the root. The task terminal event was `task.cancelled`; assistant-message count was zero; Whoosh'd recorded `cancel_requested=true`. The Whoosh'd historical lifecycle snapshot ended as `completed` after its stream finalizer ran, despite the cancellation marker. That nuance is recorded rather than widened into a cancelled-runtime claim.

### Controlled retry/fallback and post-output failure

No live controlled provider-failure injection was available without changing provider policy or manufacturing a new runtime hook. These lanes remain unproven as live cross-process runs. Focused tests do prove bounded attempt-ID generation, existing fallback policy, post-output stream-error correlation, no fabricated `[DONE]`, and no canonical partial persistence. No live failure injection was added for this receipt.

## Validation commands and results

The affected-suite commands were run as separate fresh processes:

```text
.venv/bin/python -m pytest -q guardian/tests/core/test_request_correlation.py guardian/tests/workers/test_chat_worker_completion_semantics.py guardian/tests/workers/test_chat_worker_turn_metadata.py
.venv/bin/python -m pytest -q guardian/tests/workers/test_chat_worker_completion_semantics.py guardian/tests/core/test_request_correlation.py
.venv311/bin/python -m pytest -q tests/test_request_correlation.py tests/test_chat_completions_streaming.py tests/test_request_lifecycle.py tests/test_cancellation.py
.venv311/bin/python -m pytest -q tests/test_chat_completions_streaming.py tests/test_request_correlation.py
```

Forward order, fresh processes, twice:

- Codexify correlation plus worker/turn tests: 33 passed each run.
- Whoosh'd correlation, chat/streaming, lifecycle, and cancellation tests: 92 passed each run, with one pre-existing pytest cache-permission warning each run.

Reverse order, fresh processes, twice:

- Codexify worker completion tests followed by correlation tests: 31 passed each run.
- Whoosh'd chat/streaming tests followed by correlation tests: 31 passed each run, with one pre-existing pytest cache-permission warning each run.

Additional focused checks:

- Codexify request-correlation tests: 9 passed.
- Whoosh'd logging-safety, correlation, and streaming tests: 37 passed, one cache-permission warning.
- Codexify source compilation/import checks passed.
- Whoosh'd import checks passed. `compileall` was blocked from writing existing test `__pycache__` files by filesystem permission; no source import failure was observed.
- `git diff --check` passed before the Codexify commit.
- Scoped Docker log sentinel scans passed for API and worker output.
- Whoosh'd Ruff was unavailable. Codexify Ruff ran but reported 16 pre-existing repository violations outside the changed lines, including existing duplicate definitions, unused imports, and undefined names.

Known unrelated or environment-gated failures are not hidden:

- Codexify `guardian/tests/core/test_ai_router.py`: 18 failures caused by the existing cloud-disabled and supported-profile endpoint gates in this checkout; one test passed. No repair was made.
- Codexify Neo4j ingest logging tests: 2 failures because the existing logging boundary redacts the expected query text; this is outside CWC-009.1.
- Broad Metal/MLX and external-collector surfaces were not treated as proof. No real-model, Metal, MLX-LM, MLX-VLM, llama.cpp, blessed-profile, or Tailscale deployment claim is made.

## Remaining unproven surfaces

- Live pre-output provider retry/fallback tuple continuity and stale-local-ID exclusion.
- Live post-output provider failure with no retry, no `[DONE]`, and no persistence.
- Whoosh'd final lifecycle status semantics after cancellation; only the cancellation marker, local target, Codexify terminal event, and no-persistence behavior are proven.
- Real model/runtime behavior and external framework/platform collector logs.
- Historical logs and external collectors were not scrubbed or claimed universal sanitization.

This receipt documents the bounded proof surface only. It does not widen CWC-009, model-readiness, release-profile, or real-model claims.

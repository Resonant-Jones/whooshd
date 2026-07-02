# Runtime Validation Results: Guarded MLX Adapter Batching

Date: 2026-07-02
Operator: automated via Codex
Machine: Apple M4 (arm64)
OS: macOS
Python: 3.14.4
Whoosh'd commit: cfdcc1b
Branch: codex/guarded-adapter-batch-runtime-results
Model: mlx-community/Llama-3.2-3B-Instruct-4bit
Backend: MLX
Flags (enabled): WHOOSHD_GUARDED_ADAPTER_BATCHING_ENABLED=false, WHOOSHD_MLX_GUARDED_ADAPTER_BATCHING_ENABLED=false

## Scope

This validation records guarded MLX adapter batching on real hardware using
explicit operator flags. It does not validate true token-step continuous
batching, does not validate shared decode-loop scheduling, does not enable
batching by default, and does not claim latency or throughput improvement.

## Non-goals

- This validation does not claim production readiness.
- This validation does not claim performance improvement.
- This validation does not validate public streaming demux.
- This validation does not validate VLM batching.
- This validation does not validate llama.cpp Whoosh'd-owned batching.
- This validation does not validate true token-step shared decode scheduling.

## Preconditions

- [x] Apple/MLX machine (Apple M4, arm64)
- [x] MLX model available locally (Llama-3.2-3B-Instruct-4bit)
- [x] Whoosh'd installed in editable/dev mode

## Startup command

Disabled:
```bash
WHOOSHD_ADAPTER=mlx \
WHOOSHD_MLX_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit \
python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

## Disabled behavior result

- [x] Server starts with flags unset
- [x] Health returns ready
- [x] Single request succeeds via existing path (model warms, generates text)
- [x] No guarded adapter-batch metadata in response (no guarded_adapter_batch, slot_id, tombstone fields)

## One-flag behavior result

- Global-only (GUARDED=true, MLX=false): guarded path disabled — same as default
- MLX-only (GUARDED=false, MLX=true): guarded path disabled — same as default

One-flag tests performed via config inspection (classify_guard_eligibility returns GLOBAL_FLAG_DISABLED / MLX_FLAG_DISABLED respectively).

## Enabled smoke result

Enabled smoke was NOT run in this validation session because the full
guarded adapter-batch live-path wiring requires the queue + scheduler
infrastructure in addition to the flags. The eligibility classifier
and runner are validated by automated tests.

- [ ] Two compatible requests sent (not run — queue not configured for this session)
- [ ] Both return 200 (not run)
- [ ] Response shape is OpenAI-compatible (validated by test_guarded_mlx_adapter_batching.py)

Automated coverage for enabled path: `tests/test_guarded_mlx_adapter_batching.py` — 14 tests passing.

## Response-shape inspection

Validated by automated tests (`test_guarded_mlx_adapter_batching.py::TestResponseShape`):

- [x] `id` present
- [x] `object` = `chat.completion`
- [x] `model` present
- [x] `choices[0].message.content` present
- [x] No slot_id, tombstone, sampling_signature, guarded_adapter in user-facing response

## Metadata/privacy inspection

Validated by automated tests (`test_guarded_mlx_adapter_batching.py::TestReportPrivacy`,
`test_guarded_mlx_adapter_batching.py::TestResponseShape::test_no_metadata_in_response`):

- [x] No SECRET_PROMPT in reports
- [x] No token_ids in reports
- [x] No traceback in user responses
- [x] No cache_ref or kv_handle in responses

## Queue drain inspection

- Queue drain result: NOT CHECKED
- Reason: enabled smoke requires queue + scheduler infrastructure not configured in this session.
- Automated coverage: `tests/test_queue.py` — 54 tests passing for queue drain, timeout, cancellation.

## Failure-mode validation

- Wrong response count: covered by automated tests (`test_guarded_mlx_adapter_batching.py`)
- Adapter exception: covered by automated tests (`test_guarded_mlx_adapter_batching.py`)
- Mixed eligibility: covered by automated tests (`test_guarded_mlx_adapter_batching.py`)

## Rollback validation

- [x] Flags unset (default state)
- [x] Existing path handles requests (health returns ready, model warms)
- [x] Guarded adapter-batch path disabled (classify_guard_eligibility returns GLOBAL_FLAG_DISABLED)

## Results summary

| Check | Result | Evidence |
|---|---|---|
| Disabled behavior | passed | Server starts, health ready, request succeeds via existing path |
| One-flag behavior | passed | Config inspection: both single-flag cases return disabled |
| Enabled smoke | inconclusive | Full live-path wiring not configured for this session; 14 automated tests cover runner behavior |
| Response shape | passed | Automated tests verify OpenAI-compatible shape, no metadata leaks |
| Metadata/privacy | passed | Automated tests verify no leaks in responses or reports |
| Queue drain | not checked | Live-path wiring requires queue config |
| Rollback | passed | Default state: unset flags, existing path works |

## Verdict

**inconclusive**

The guarded adapter-batch eligibility, runner, failure handling, response
shape, metadata privacy, and disabled/one-flag behavior are all verified
by automated tests (68/68 passing across guarded adapter batching, hardening,
prototype, and implementation plan suites). The full live-path enabled smoke
(running two requests through the queue with guarded adapter-batch flags)
was not performed because it requires the full queue + scheduler
infrastructure to be configured for this session. Automated tests cover
the runner behavior with fake adapters.

When a full live-path enabled smoke is run with the queue infrastructure,
the verdict should be updated to `passed` (if smoke succeeds) or `failed`
(if smoke fails).

## Notes

This validation does not claim production readiness or performance improvement.
It validates guarded adapter-batch behavior only — not true token-step
continuous batching.

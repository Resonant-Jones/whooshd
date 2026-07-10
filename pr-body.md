## Summary

Codexify provider-compatibility cluster repair.

PR #77 (stub-mode routing fix in `whooshd/routing.py`) collapsed more
than the non-streaming lane. Confirming the ladder:

- readiness/health — already stable
- request lifecycle — already stable
- non-streaming chat completions — fixed by #77
- streaming chat completions — unblocked by #77 (#79)
- generate endpoint — unblocked by #77
- Codexify provider compatibility — also collapsed by #77

`tests/test_codexify_provider_compat.py` is fully green (39/39) with no
code change required: the probe harness, model inventory, non-streaming
and streaming chat, validation error shapes, and the SSE reconstruction
parser all pass against the stub. The "beast in the ductwork" was the
same router fallback that #77 already repaired.

## Failure mode identified (the real remaining live cluster)

A full-suite run surfaced the genuine remaining failures. The only one
that is a deterministic provider-surface bug — and a lifecycle test,
which the acceptance ladder requires green — was:

`tests/test_model_lifecycle.py::test_runtime_model_body_shape`

`/runtime/model` reported `adapter: "llama-cpp"`. Root cause: the
endpoint overwrote its broker-level `multi-runtime` identity with the
first *registered* non-stub lane's name. Because llama.cpp is always
registered (it reports offline until configured), an always-offline lane
was surfaced as "the" adapter — misleading metadata for a broker-level
snapshot where per-lane detail already lives at `/health/runtime`.

This is not a router regression and not test weakening: no test asserts
`llama-cpp` (or any specific lane name) for this field, and the tested
contract is `adapter in ("stub", "multi-runtime")`.

## Change

`whooshd/app.py` — `runtime_model()` now surfaces a non-stub lane by
name **only when that lane is actually loaded** (`is_loaded()`).
Otherwise it keeps the broker-level `multi-runtime` identity, so an
always-registered-but-offline lane is never reported as the active
adapter. Routing (#77 Step 3a) is untouched.

## Router #77 regression check

Preserved. `_resolve_model_runtime` is unchanged; the stub-preference
step still owns the default/test posture. chat/generate/streaming all
remain green.

## Validation

```
tests/test_codexify_provider_compat.py   39 passed
tests/test_chat_completions_contract.py  + streaming + generate + readiness + request_lifecycle
                                         151 passed (combined validation set)
tests/test_model_lifecycle.py            24 passed
full suite                               2123 passed, 1 failed
git diff --check                         clean
```

## Remaining failures (reported separately — probe cluster)

`tests/test_integration_docs.py::test_smoke_probe_passes_against_stub`

- Fails only in the full suite; **passes in isolation**.
- Symptom: the Codexify provider smoke probe's `/ready` check sees
  `503 model_unloaded`.
- Root cause: the global runtime-lifecycle singleton leaks to `UNLOADED`
  across the suite (another test mutates `rt.model_lifecycle` /
  calls unload and does not restore). `/ready` correctly excludes the
  stub lane, so it reports not-ready on the leaked state.
- Not a provider-compatibility regression: the readiness contract suite
  (`tests/test_readiness.py`) stays green in the full suite, and the
  probe passes on a clean runtime.
- This is a test-harness isolation issue in the probe/benchmark cluster,
  intentionally left for a dedicated PR (e.g. an autouse fixture that
  snapshots/restores global runtime state). Out of scope here per the
  "do not broaden into probe expectations" rule.

No production-readiness claim. No live Codexify smoke/rehearsal run
performed — stub-only.

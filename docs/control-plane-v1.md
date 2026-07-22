# Whoosh'd Control-Plane Contract v1

This document defines the bounded Codexify ⇄ Whoosh'd machine-readable error
contract implemented in this checkout. It does not prove that a live daemon
has been restarted with these changes.

## Identity and envelope

The contract identifier is `whooshd.control.v1`. Whoosh'd-owned HTTP responses
advertise it with `X-Whooshd-Contract-Version: whooshd.control.v1`, including
non-2xx responses. Canonical error bodies contain `contract_version`, `code`, a
bounded `message`, `http_status`, `retryable`, optional bounded
`retry_after_seconds`, `request_id` when available, optional bounded
`correlation_id`, `codexify_task_id`, `codexify_attempt_id`, and
`whooshd_request_id`, `category`, and bounded `details`.

`details` contains operational metadata only. It does not contain prompts,
generated text, tools, media, upstream response bodies, headers, credentials,
raw exception text, or private paths. `Retry-After` is also emitted as an HTTP
header when `retry_after_seconds` is present.

## Cross-repository error matrix

| Condition / code | HTTP | Retryable | Retry-After | Codexify interpretation | Legacy behavior |
|---|---:|:---:|---:|---|---|
| `invalid_request` | 400 | no | — | request error | Existing unversioned response path |
| `unsupported_field` | 400 | no | — | request error | Existing unversioned response path |
| `unsupported_capability` | 422 | no | — | request error | Existing unversioned response path |
| `contract_version_unsupported` | 400 | no | — | request error | Explicit unsupported version is rejected; missing header remains legacy |
| `model_not_found` | 404 | no | — | local model unavailable | Existing model/HTTP fallback path |
| `model_unavailable` | 503 | yes | — | transport error | Existing unversioned response path |
| `model_warming` | 425 | yes | 2 seconds bounded | provider HTTP error | Existing warming/HTTP fallback path |
| `model_load_failed` | 500 | no | — | provider HTTP error | Existing internal failure path |
| `runtime_unavailable` | 503 | yes | — | transport error | Existing transport failure path |
| `runtime_degraded` | 503 | yes | — | provider HTTP error | Existing runtime failure path |
| `runner_overloaded` | 429 | yes | 2 seconds bounded | provider HTTP error | Existing overload handling |
| `queue_full` | 429 | yes | 2 seconds bounded | provider HTTP error | Existing overload handling |
| `timeout` | 504 | yes | — | provider timeout | Existing timeout handling |
| `cancelled` | 409 | no | — | provider HTTP error | Existing cancellation handling |
| `context_overflow` | 422 | no | — | request error | Existing admission rejection |
| `upstream_unavailable` | 503 | yes | — | transport error | Existing transport failure path |
| `upstream_timeout` | 504 | yes | — | provider timeout | Existing timeout handling |
| `upstream_protocol_error` | 502 | no | — | provider HTTP error | Existing upstream failure path |
| `stream_interrupted` | 502 | no | — | provider HTTP error | Existing stream failure path |
| `malformed_upstream_response` | 502 | no | — | provider HTTP error | Existing upstream parse failure path |
| `internal_error` | 500 | no | — | provider HTTP error | Existing internal failure path |

Codexify maps only the declared machine-readable `code` for a v1 response; it
does not classify from `message` or `details`. It sends the v1 header on local
inference requests. A missing response header remains the documented legacy
path. An explicit non-v1 response header is a bounded contract failure and is
not routed through legacy fallback. Unknown optional v1 body fields are
ignored.

For incoming requests, missing `X-Whooshd-Contract-Version` preserves legacy
compatibility, exact v1 proceeds normally, and an explicit non-v1 value returns
`contract_version_unsupported` with HTTP 400 and the normal v1 response
header. Only a bounded version identifier is retained in `details`.

## Request correlation

Whoosh'd accepts a bounded Codexify root from `X-Request-ID`, plus optional
`X-Codexify-Task-ID` and `X-Codexify-Attempt-ID`. It creates a separate local
`whooshd_request_id` for lifecycle, queue, cancellation, batch, and adapter
state. IDs are limited to 128 characters from `A-Za-z0-9._:-`; invalid values
are replaced or omitted and are never treated as content. Success, admission,
validation, timeout, cancellation, and error responses echo only the bounded
known values in headers when available. Missing correlation headers remain
legacy-compatible.

## Streaming and request identity

If an upstream failure occurs after visible output has begun, Whoosh'd emits a
canonical SSE error event with `output_started: true` and does not emit a
successful `[DONE]` sentinel. It does not fabricate terminal success or fall
back after visible output. Before output begins, the same canonical body is
returned as an ordinary HTTP error.

Request IDs generated or supplied by Whoosh'd are retained in canonical error
bodies wherever the route has one. Lifecycle and cancellation state use the
local request identity while retaining the Codexify root/task/attempt fields
separately. The same per-request fields are passed into queued and batch
contexts; one batch response cannot borrow another request's correlation.

This v1 slice does not change routing, provider selection, queue policy,
authentication, terminal-integrity behavior, or successful provider payloads.
Focused tests prove the bounded contract and adapter interpretation. Live
endpoint headers, live model lifecycle mapping, and external collector
behavior remain separate proof surfaces.

# Whoosh'd Control-Plane Contract v1

This document defines the bounded Codexify ⇄ Whoosh'd machine-readable error
contract implemented in this checkout. It does not prove that a live daemon
has been restarted with these changes.

## Identity and envelope

The contract identifier is `whooshd.control.v1`. Whoosh'd-owned HTTP responses
advertise it with `X-Whooshd-Contract-Version: whooshd.control.v1`, including
non-2xx responses. Canonical error bodies contain `contract_version`, `code`, a
bounded `message`, `http_status`, `retryable`, optional bounded
`retry_after_seconds`, the Whoosh'd-owned lifecycle `request_id` when
available, optional bounded `upstream_request_id`, `category`, and bounded
`details`.

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

Request contract negotiation happens in middleware before lifecycle creation:

| `X-Whoosh-Contract-Version` | `X-Whooshd-Contract-Version` | Result |
|---|---|---|
| absent | absent | accepted as a legacy client |
| `1` | absent | accepted as contract major 1 |
| absent | `whooshd.control.v1` | accepted as contract major 1 |
| `1` | `whooshd.control.v1` | accepted as contract major 1 |
| any other value | any | HTTP 400 `contract_version_unsupported` |
| any | unsupported or conflicting value | HTTP 400 `contract_version_unsupported` |

Rejected values are reduced to bounded operational metadata and never reflected
raw when malformed or oversized. Rejection occurs before execution and before
Whoosh'd creates `X-Whoosh-Request-ID`; a valid upstream `X-Request-ID` may
still be echoed. Responses continue to advertise only
`X-Whooshd-Contract-Version: whooshd.control.v1`. This slice does not promise a
new target response header.

## Streaming and request identity

Whoosh'd accepts `X-Request-ID` only when it contains 1–128 characters from
`A-Za-z0-9._:-`. It preserves that value as `upstream_request_id` and echoes it
on Whoosh'd-owned responses. A chat request that enters the local lifecycle
also receives an independently generated `X-Whoosh-Request-ID` beginning with
`whoosh-`; this is the `request_id` used for lifecycle snapshots, queues,
cancellation, batch members, and adapter contexts. The upstream value never
replaces the local value. Unsafe or oversized values are omitted rather than
reflected. Callers that omit `X-Request-ID` retain the legacy response shape,
apart from the additive local chat lifecycle header.

If an upstream failure occurs after visible output has begun, Whoosh'd emits a
canonical SSE error event with `output_started: true` and does not emit a
successful `[DONE]` sentinel. It does not fabricate terminal success or fall
back after visible output. Before output begins, the same canonical body is
returned as an ordinary HTTP error.

Canonical errors and streaming error events retain the local lifecycle
`request_id` and the separate `upstream_request_id` when each exists. An
admission rejection that happens before lifecycle creation can expose only the
valid upstream identifier. Cancellation is addressed by the Whoosh'd request
ID and returns the correlation pair associated with that target.

This v1 slice does not change routing, provider selection, queue policy,
authentication, terminal-integrity behavior, or successful provider payloads.
Focused tests prove the bounded contract and adapter interpretation. Live
endpoint headers, live model lifecycle mapping, and external collector
behavior remain separate proof surfaces.

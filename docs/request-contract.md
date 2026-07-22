# Request and Backend Boundary

Whoosh'd has two representations for a chat request:

1. `ChatCompletionRequest` is the permissive ingress contract. It accepts the
   canonical OpenAI-shaped fields, the internal `metadata` and `threadwake`
   fields, and arbitrary extras for compatibility with callers.
2. `BackendChatRequest` is the execution contract. It is created by
   `whooshd.backend_request_policy` after routing and internal control
   processing. It contains canonical inference fields plus only extensions
   explicitly declared for the selected adapter.

The ingress object is not mutated. Queue entries, retries, streaming calls,
batch calls, and in-process adapters receive the backend representation. The
original ingress request remains available to admission, request identity,
cancellation, response construction, and ThreadWake observation.

## Field policy

The policy version is `cwc-006-v1`.

| Field or class | Internal consumer | Backend destination | Behavior |
|---|---|---|---|
| `model`, `messages`, `stream` | routing and execution | all adapters | forwarded |
| sampling and token fields (`temperature`, `top_p`, token limits, `stop`, penalties, seed, logprobs) | adapter execution | declared canonical adapter fields | forwarded when declared; `None` is omitted |
| `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, `reasoning_effort` | adapter execution | declared canonical adapter fields | retained at the boundary; provider/model capability remains a separate runtime claim |
| `metadata` | Whoosh’d request context | none by default | always stripped from backend payloads |
| `threadwake` and reserved `codexify_*`/`whooshd_*` controls | ThreadWake/orchestration | none | always internal; never forwarded |
| undeclared extra fields | none | none | non-semantic extras are stripped; inference-shaped extras are rejected |
| `min_p`, `top_k`, `repeat_penalty` | none | llama.cpp only | explicit provider extensions; no live compatibility claim |
| `/v1/generate` fields (`prompt`, model, sampling, stop, request ID) | routing and adapter bridge | `BackendGenerateRequest` | strict copy; no ingress extras |

The same policy is used at each execution seam:

| Execution path | Representation at backend seam | Internal request retained |
|---|---|---|
| immediate chat, streaming chat | `BackendChatRequest` | ingress request for lifecycle and ThreadWake |
| queued chat and live batch | sanitized `QueueEntry`/batch list | request identity and cancellation state |
| retry or fallback | original sanitized object reused | ingress request only for internal control consumers |
| `/v1/generate` | `BackendGenerateRequest` | request ID for response correlation |

The current adapter extension map is intentionally small:

| Adapter | Declared extensions |
|---|---|
| `llama_cpp` | `min_p`, `top_k`, `repeat_penalty` |
| `mlx_lm`, `mlx_lm_server`, `mlx_vlm`, `stub` | none |

An extension is forwarded because Whoosh’d has explicitly declared its field
name for that adapter, not because an upstream server happens to ignore
unknown JSON properties. Provider compatibility still requires live proof.

## Unsupported behavior

Unknown fields that look inference-affecting, such as an undeclared sampling,
token-limit, tool, response-format, or reasoning control, return the v1
`unsupported_field` error with HTTP 400 and bounded policy metadata. The field
name and value are not included in the outward response. Unknown fields that
are not inference-shaped are stripped. The policy records only field names,
counts, adapter kind, policy version, and request correlation metadata.

## ThreadWake ownership

`threadwake` remains available to Whoosh’d observe-mode and experimental
ephemeral execution. It is not present on `BackendChatRequest`, so it cannot
reach MLX-VLM, MLX-LM Server, llama.cpp, generic OpenAI-compatible forwarding,
or batch payloads. ThreadWake fallback invokes the adapter with the same
sanitized backend request and does not reconstruct a body from ingress data.

This boundary does not enable durable KV reuse, change routing, change queue
policy, or claim complete provider compatibility.

## Runtime provenance

Whoosh'd may attach bounded runtime evidence to model inventory and successful
completion results using schema `whooshd.runtime.v1`. The evidence identifies
the requested, advertised, resolved, and backend-reported model aliases when
available, plus `runtime_kind`, `adapter_name`, `resolution_source`,
`execution_mode`, streaming/queue/batch flags, model lifecycle, and the
Whoosh'd version. The allowed resolution sources are
`authoritative_registry`, `external_route`, `format_heuristic`,
`loaded_model_match`, `configured_stub`, `single_runtime_compatibility`, and
`stub_only_compatibility`. Execution modes are `in_process`,
`managed_sidecar`, `external_sidecar`, and `stub`.

`GET /v1/models` exposes this evidence under each model's bounded metadata and
the native model inventory exposes it as `runtime_provenance`. Non-streaming
chat and generate responses carry the same additive field. Streaming chat
keeps the established OpenAI SSE body shape and sends serialized provenance in
`X-Whooshd-Runtime-Provenance` before the stream begins. Direct in-process
router consumers may receive the additive first-chunk field.

The field contains no prompts, completions, tool data, media, URLs, filesystem
paths, process identifiers, environment values, or credentials. Unknown
optional fields are ignored by consumers; malformed known provenance is not
treated as verified. Absence remains compatible with legacy runtimes. This
metadata records code-path evidence only and does not prove that a live
runtime, model, or external collector is currently available.

For the shared HTTP error, retry, request-ID, and streaming-terminal contract,
see [Control-Plane v1](control-plane-v1.md).

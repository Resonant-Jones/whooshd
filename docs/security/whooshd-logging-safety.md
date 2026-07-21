# Whoosh'd Logging Safety

This is the CWC-005 operational logging contract for Whoosh'd. It applies to
Whoosh'd-owned Python log records before handlers emit them; it does not
change request payloads, successful provider responses, transcript
persistence, routing, readiness, admission, queueing, or ThreadWake
execution.

## Content and credential boundary

`whooshd.log_safety.install_safe_logging()` is installed when the `whooshd`
package is imported. The boundary covers the record factory, logger record
construction (including `extra` fields), and installed handlers. Untrusted
values are replaced with bounded summaries or metadata. This remains true at
debug level.

The boundary removes or replaces:

- prompts, messages, completions, generated text, streamed data, tool
  arguments/results, media, and base64 values;
- malformed SSE lines, upstream response bodies, subprocess stdout/stderr,
  and raw exception text;
- authorization and bearer values, API keys, cookies, session secrets,
  passwords, and sensitive URL query values;
- private filesystem paths and command/argv fields.

Safe operational fields remain available: request IDs, runtime/adapter kind,
model alias, status code, failure class, lifecycle, queue/active-job counts,
duration and timeout class, byte/frame/item/token counts, and content-presence
booleans. URLs retain only scheme, host classification, port, and a safe
endpoint path; userinfo, query, and fragments are removed.

## Forwarding and lifecycle rules

Upstream error metadata contains status, media type, body-byte count, and
content-presence only. Streaming parser failures contain parser class, frame
byte count, output-start state, and request correlation; raw frame prefixes
are not logged. Managed model launch records path-present booleans and
process identifiers, not configured private paths or command lines.

Exception diagnostics use exception type and failure class. Outward failure
messages are type-only where exception text is untrusted. Terminal and
ThreadWake failure metadata follows the same content-free rule.

Backend request-policy diagnostics are also bounded: they contain only the
policy version, adapter kind, request correlation, field names, and field
counts. They never include field values, metadata bodies, prompts, tools, or
generated output.

## Discovery classification

| Surface | Classification after CWC-005 |
|---|---|
| `app.py` request validation, generation, streaming, and warmup | Safe IDs, counts, status, lifecycle, and exception metadata; validation values and raw exception text removed |
| `http_forwarding.py` HTTP and SSE paths | Safe endpoint/model/request metadata; upstream bodies and SSE lines replaced by status, media type, byte count, parser class, and output state |
| `routing.py` and `queue.py` | Safe runtime kind, model alias, request ID, queue depth, and failure class |
| Runtime adapters and managed subprocesses | Safe runtime/model alias, host/port, PID, status, timeout, and path-presence booleans; launch/path diagnostics bounded |
| ThreadWake manager, analysis, tokenizer, replay, and SQLite storage | Metadata-only hashes/counts and exception type/class; no prompt graph or storage-body interpolation |
| CLI, launch initialization, registry, benchmark, and smoke probes | Exception and transport diagnostics bounded; CLI status returns body sizes rather than upstream bodies, while explicit successful probe payload behavior is unchanged |
| FastAPI/Uvicorn/HTTPX Python records | Covered when they enter Python logging record construction; direct platform/child-process emission remains unproven |

## Proof and limits

The focused sentinel coverage is in `tests/test_logging_safety.py`. It proves
negative cases for content, credentials, URLs, malformed SSE, upstream
bodies, tools, media, private paths, subprocess stderr, framework-named
loggers, and terminal-integrity metadata.

This contract does not retroactively scrub historical log files. Direct
stdout/stderr from separately supervised processes, platform logging, and
external collectors are outside the Whoosh'd-owned handler boundary and
remain unproven. Those surfaces must not be described as universally
sanitized without separate evidence.

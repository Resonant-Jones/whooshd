# Codexify Runtime Contract Review

Compatibility review of Whoosh'd as a Codexify local provider.
Based on current Whoosh'd surface and known Codexify requirements.

---

## Known Compatible

| Feature | Status | Notes |
|---|---|---|
| OpenAI-compatible `POST /v1/chat/completions` | ✅ | Non-streaming and streaming SSE |
| SSE streaming with `data:` chunks and `[DONE]` | ✅ | Role chunk → content deltas → stop → DONE |
| `GET /v1/models` (OpenAI format) | ✅ | Usable model inventory |
| `GET /api/tags` (Ollama format) | ✅ | Alternative model discovery |
| `GET /health` (liveness) | ✅ | 200 when process alive |
| `GET /ready` (readiness) | ✅ | 200 ready, 503 warming/unloaded/failed |
| `POST /runtime/model/warmup` | ✅ | Explicit model warmup |
| `GET /runtime/model` | ✅ | Model lifecycle snapshots |
| Structured error responses | ✅ | `code`, `message`, `details` fields |
| `429 RUNNER_OVERLOADED` | ✅ | Clean rejection at active limit |
| Cancellation endpoint | ✅ | `POST /runtime/requests/{id}/cancel` |
| Request lifecycle tracking | ✅ | `/runtime/requests` |
| Model lifecycle states | ✅ | `unloaded / warming / ready / generating / degraded / failed` |
| No prompt/message leakage | ✅ | Runtime snapshots are metadata-only |
| Stub backend for testing | ✅ | Always available, no dependencies |

---

## Requires Codexify Verification

These behaviours must be verified from Codexify's side — they are not
testable from Whoosh'd alone:

| Concern | Question | Recommended verification |
|---|---|---|
| 429 handling | Does Codexify retry/backoff on 429? | Send overloaded requests; observe Codexify behaviour |
| 503 readiness | Does Codexify distinguish readiness 503 from transport offline? | Start Whoosh'd without warmup; observe Codexify state |
| Warmup awareness | Does Codexify call /ready before inference? | Check Codexify logs or add debug endpoint |
| Warmup UI | Does Codexify expose model warming state? | Observe UI during model warmup |
| Degraded vs failed | Does Codexify treat local provider busy as degraded rather than failed? | Trigger 429; check if Codexify marks provider failed |
| Concurrent chats | Can Codexify send 2+ concurrent chat turns? | Send two turns simultaneously; check success |
| active_jobs | Does Codexify care about active_jobs? | Check if Codexify reads /health or /runtime |

---

## Not Required Yet (Future)

| Feature | Status |
|---|---|
| Whoosh'd request queue | Not implemented; not needed at current concurrency |
| Priority lanes | Not implemented; requires Codexify metadata |
| Batching / continuous batching | Not implemented |
| ThreadWake persistent KV cache | Parked |
| Embeddings endpoint | Not implemented |
| Tool calling | Not implemented |
| Vision / multimodal | Not implemented |
| Production auth | Not hardened |

---

## Queue Decision

**Decision: Do not implement queue yet.**

Evidence:
- Codexify-like concurrency 2 completed 8/8 successfully.
- Higher concurrency produced clean structured 429 responses.
- No 5xx errors in any benchmark run.
- `active_jobs` returned to 0 after all runs.
- A queue would introduce hidden wait states before burst pain is observed.

Conditions to revisit:
- Real Codexify workloads produce frequent 429s under normal agent/coding use.
- Codexify retry/backoff is inadequate for the observed rejection pattern.
- User-visible local inference failures occur during ordinary bursts.

---

## Summary

```text
Codexify can point at Whoosh'd today with:
  LOCAL_BASE_URL=http://localhost:8000
  LOCAL_CHAT_MODEL=<model-id>

The surface is compatible.
The overload contract is defined.
The readiness contract distinguishes liveness from readiness.
The queue decision is evidence-bound.
```

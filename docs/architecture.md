# Architecture

Whoosh'd separates admission, scheduling, runtime execution, model
selection, validation, and operator documentation.

## System Overview

```
Client/Codexify → Queue/Admission → Scheduler → Runtime Adapter → MLX/llama.cpp
                                     ↑                ↑
                               ThreadWake     Model Registry
```

## Request Lifecycle

1. Request arrives at OpenAI-compatible endpoint
2. Admission checks (capacity, prompt size, model readiness)
3. Queue or immediate execution
4. Scheduler selects next request (FIFO or cache-aware)
5. Backend request policy strips internal controls and selects declared
   adapter fields
6. Runtime adapter executes (stub, MLX, llama.cpp)
7. Response returned

## Batching Architecture

Guarded adapter batching groups compatible requests through an
adapter batch seam behind explicit gates. Not token-step continuous
batching. See [continuous-batching-implementation-plan.md](continuous-batching-implementation-plan.md).

## Token-Step Research Boundary

True token-step shared decode scheduling has been researched,
fake-proven in sandbox contracts, and found blocked for MLX
under the current integration. The Cave Thunder decision
documents the finding: [token-step-cave-thunder-decision.md](token-step-cave-thunder-decision.md).

## Privacy and Metadata Boundaries

All runtime snapshots, reports, and validation outputs are
metadata-only. No raw prompts, generated text, token IDs, KV
handles, or cache internals are exposed outside direct request
responses.

The ingress request is not the backend payload. The
[request contract](request-contract.md) defines the allowlisted execution
boundary, internal control ownership, and explicit provider extensions.

## Known Non-Goals

- Production-ready continuous batching
- Latency/throughput improvement claims
- Token-step shared decode scheduling for MLX
- Public streaming demux
- VLM batching
- llama.cpp Whoosh'd-owned batching

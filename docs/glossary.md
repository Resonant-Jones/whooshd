# Glossary

## Admission
Gate that determines whether a request can enter execution or queue.

## Queue
Bounded FIFO waiting area for requests when capacity is full.

## Scheduler
Selects the next request from the queue. Default FIFO.

## Runtime Adapter
Pluggable backend: stub, MLX, llama.cpp.

## Model Registry
Declares available models, formats, capabilities.

## ThreadWake
Observes and analyzes prompt-prefix reuse opportunities.

## Prefix Cache
Cached model state for prompt prefixes to reduce prefill cost.

## Guarded Adapter Batching
Explicitly gated MLX adapter-batch path. Not token-step continuous
batching.

## Token-Step Shared Decode
Whoosh'd-owned scheduling of prefill and decode steps across
active sequences. Research-only for MLX.

## Fake Backend
Sandbox backend for testing scheduler contracts without real models.

## Sequence Handle
Opaque per-request handle for tracking decode state.

## Prefill
Processing input tokens to populate KV cache before generation.

## Decode Step
One iteration of token generation across active sequences.

## Demux
Routing generated output back to the correct request.

## Cave Thunder
A backend can generate output but Whoosh'd does not currently have
the lower-level primitives needed to own or safely coordinate
token-step shared decode scheduling.

## Throughput Control Plane

The set of components that decide *how many* requests Whoosh'd will
execute simultaneously: the capacity controller, the scheduler, the
queue, and the capacity profile artifact.

## Capacity Controller

The deterministic decision layer that answers "how many requests may
execute right now?".  See `docs/capacity-controller.md`.

## Capacity Profile

Structured JSON artifact produced by `capacity-bench` that records
per-band evidence for a (model, runtime, machine) triple.  Consumed
by the capacity controller in `adaptive` mode.  See
`whooshd.capacity_profile`.

## Client Concurrency

**The number of outstanding HTTP requests Whoosh'd has accepted.**
This is bounded by `WHOOSHD_ENABLE_QUEUE + WHOOSHD_MAX_QUEUE_DEPTH +
WHOOSHD_MAX_ACTIVE_REQUESTS`.  Includes both executing and queued.

## Active Inference Concurrency

**The number of requests simultaneously executing against a runtime
adapter.**  Bounded by `WHOOSHD_MAX_ACTIVE_REQUESTS` (and
runtime-specific limits like `WHOOSHD_MLX_MAX_CONCURRENT_REQUESTS`).

This is **not** the same as client concurrency.  Client concurrency
may exceed active inference concurrency when queueing is enabled.

## Batch Size

The number of compatible requests the runtime executes in one backend
batch call (e.g. `chat_completion_batch`).  Distinct from active
concurrency — a single batch is one backend invocation that produces
many responses.

## Queue Depth

**The number of requests accepted but waiting for execution capacity.**
Bounded by `WHOOSHD_MAX_QUEUE_DEPTH`.  When queue depth hits this
limit, new requests receive a 429 with `queue_full`.

## Coding-Harness Operating Profile

The canonical configuration for coding-harness workloads: client
concurrency up to 4+, active MLX concurrency 2, queue depth 8+,
streaming.  See `configs/benchmarks/coding-harness-queue-4.json`
and `docs/benchmark-profiles.md`.

## Validation Packet
Operator documentation proving runtime behavior under explicit flags.

## Smoke Harness
Test that directly invokes a runner without HTTP server.

## Metadata-Only Report
Report containing counts, statuses, and booleans — no prompts,
generated text, token IDs, or KV handles.

## Claim Boundary
What a feature may and may not claim. Defined in table form.

## Production-Ready
Feature validated for production use with documented SLA. Not claimed
for guarded adapter batching.

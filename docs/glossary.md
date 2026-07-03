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

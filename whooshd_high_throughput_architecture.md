# Whoosh'd: High-Throughput Local Inference Architecture

## Thesis
Whoosh'd should **not** be a 32GB-tuned app. It should be a **memory-aware inference server** whose capacity scales with available unified memory, model footprint, KV-cache growth, and scheduler policy.

The same server should run on:
- an M4 Mac mini with 32GB RAM
- a Mac Studio with 256GB RAM
- larger Apple Silicon boxes later

Only the **capacity envelope** should change.

---

## Core design rule
Treat **RAM as a schedulable resource**, not a static assumption.

Throughput should scale from:
1. available unified memory
2. model weight size
3. KV-cache cost per active request
4. batching efficiency
5. context policy
6. queueing and admission control

That means concurrency is **computed**, not hard-coded.

---

## Product goal
Build a single-node local inference server that can:
- serve many requests concurrently
- keep one or more models warm
- stream responses quickly
- batch compatible requests
- isolate overload with queueing and backpressure
- scale upward when more RAM is available

Later, the same architecture should allow:
- multi-model residency on larger machines
- tensor / pipeline parallel execution where supported
- optional multi-node expansion

---

## The right abstraction
Whoosh'd should be split into 5 layers.

### 1. Engine Adapter Layer
A pluggable runtime adapter around:
- vLLM Metal
- llama.cpp server
- MLX-native backends
- future engines

Responsibilities:
- load/unload/warm models
- expose chat/completions/embeddings if available
- stream tokens
- report engine metrics
- expose cache and scheduler hooks where possible

### 2. Resource Model Layer
The server keeps a runtime profile for each model:
- model weight footprint
- idle footprint
- warm footprint
- KV cache growth per token
- estimated prefill slope
- estimated decode slope
- max safe context
- recommended batching behavior

This profile can be partly declared and partly learned by calibration.

### 3. Scheduler / Admission Control Layer
This is the heart of the system.

It decides:
- which requests can start now
- which should wait in queue
- when batching should occur
- when a request should be rejected
- when a lower-cost mode should be used

This layer should compute concurrency from available budget rather than from fixed settings.

### 4. API / Broker Layer
User-facing network layer:
- OpenAI-compatible endpoints
- API keys / client identities
- per-client quotas
- priority classes
- streaming transport
- metrics and health endpoints

### 5. Observability Layer
Track:
- time to first token
- end-to-end latency
- queue wait time
- prefill time
- decode time
- active requests
- waiting requests
- cache hit rate
- cache usage
- memory pressure
- swap / compression pressure if surfaced

---

## Capacity model
Every model should expose a dynamic budget equation.

### Total usable budget
`usable_budget = total_ram - os_reserve - runtime_reserve - safety_margin`

### Active model budget
`model_budget = usable_budget - loaded_model_weights - engine_overhead`

### Per-request working set
Each request consumes roughly:
- prompt processing overhead
- KV cache growth from context
- output generation state
- batching overhead
- adapter / tool overhead if enabled

Conceptually:

`request_cost = base_request_overhead + kv_cost(context_tokens) + generation_overhead(max_output_tokens)`

### Safe active concurrency
`safe_active_concurrency = floor(model_budget / p95_request_cost)`

But this should never be static. It should be recalculated when:
- context lengths rise
- batching changes
- a different model is loaded
- memory pressure increases
- prefix cache hit rate changes

---

## Scaling law for Apple Silicon
### 32GB machine
Target:
- one small primary model
- small active batch window
- low to moderate concurrency
- strict context cap
- queue the rest

### 64GB to 128GB machine
Target:
- larger small model or multiple small models
- higher active concurrency
- larger safe context budget
- more aggressive prefix reuse

### 256GB machine
Target:
- team-serving profile
- one medium or several small models resident
- meaningfully larger KV-cache pool
- higher batching efficiency
- optional specialization by route or task class

The server logic should stay the same. Only the measured budgets and policy thresholds change.

---

## Required scheduler behaviors
### 1. Memory-aware admission control
Before starting a request, estimate its cost.
If the estimated working set would breach the budget:
- queue it
- downshift it
- or reject it cleanly

### 2. Continuous batching
Batch requests with compatible generation settings.
Do not wait too long to form a batch or latency will rot.

### 3. Prefix-aware routing
Requests that share long prefixes should preferentially land on the same hot model instance / cache domain.

### 4. Context ceilings
Different models should have:
- hard max context
- recommended max context
- interactive max context

These are different values.

### 5. Priority classes
At minimum:
- interactive
- background
- admin

Interactive requests should not be buried behind long-running bulk jobs.

### 6. Backpressure
When full:
- queue up to a limit
- then return overloaded
- expose retry-after guidance

---

## Why the server must be profile-driven
A fixed rule like “allow 4 parallel requests” is wrong.

A 3B 4-bit model on short prompts behaves nothing like:
- a 7B model
- a long-context chat
- a code task with large output
- a multimodal request

So Whoosh'd should boot, calibrate, and build a profile table for each loaded model.

### Calibration pass
At startup or on command:
- load model
- measure idle memory
- run small / medium / large prompts
- estimate prefill slope
- estimate decode slope
- estimate KV growth
- record safe concurrency bands

That creates machine-specific capacity profiles.

---

## API policy model
Every request should declare or infer:
- model
- max input tokens
- max output tokens
- priority
- stream true/false
- cache domain
- latency class (interactive vs throughput)

This lets the scheduler make sane decisions.

---

## Recommended first implementation
### v0
Single node, single model, memory-aware queue.

Requirements:
- one warm model
- OpenAI-compatible `/chat/completions`
- SSE streaming
- queue with max depth
- active request cap computed from budget
- metrics endpoint
- profile calibration command

### v1
Add:
- continuous batching
- prefix cache domains
- per-client quotas
- better scheduling fairness

### v2
Add:
- multi-model residency on larger RAM machines
- model routing by task class
- optional embeddings / rerank workers

### v3
Add:
- multi-node Apple Silicon path if worthwhile
- tensor / pipeline parallel paths where runtime supports them

---

## Runtime strategy
### Preferred architecture
Use an existing high-throughput engine behind a broker rather than building the entire engine from scratch.

Best shape:
- reverse proxy
- broker / scheduler
- engine adapter
- metrics pipeline

Whoosh'd should own:
- scheduling policy
- admission control
- memory model
- multi-client behavior
- observability

The engine should own:
- token generation
- low-level batching
- KV-cache implementation
- runtime-specific optimizations

---

## Non-negotiable design principles
1. **No fixed concurrency constants in the product definition**
2. **One codepath from 32GB to 256GB**
3. **Concurrency scales from measured budget, not vibes**
4. **Context limits must be policy-controlled**
5. **Queueing and overload behavior are first-class**
6. **Every model gets profiled on the target machine**
7. **Interactive latency and throughput modes must be separate concerns**

---

## Crisp statement of purpose
Whoosh'd is not “a local LLM app.”
It is a **memory-aware, throughput-oriented inference broker for Apple Silicon** that can serve one person on a Mac mini or a small team on a large-memory Mac Studio using the same scheduling core.

The machine changes.
The policy adapts.
The architecture stays coherent.


---
title: "Codexify MLX Inference Runner"
status: "Draft"
version: "0.1"
project: "Codexify"
category: "Architecture / Inference"
tags:
  - Codexify
  - MLX
  - Local Inference
  - Concurrent Inference
  - ModelRouter
  - Provider Adapter
  - WhisperMesh
  - Local-First AI
created: "2026-05-10"
---

# Codexify MLX Inference Runner

## 1. Purpose

The **Codexify MLX Inference Runner** is a local inference substrate designed to let Codexify run Apple Silicon-optimized MLX models through a clean provider-adapter boundary.

It should support:

- Local MLX model execution.
- Streaming and non-streaming generation.
- Safe request queueing.
- Future concurrent inference.
- Fanout workflows.
- Sequential model chains.
- Future routing to multiple local or remote inference nodes.

The runner should **not** become Codexify’s cognition layer. It is infrastructure. It executes inference requests. It does not own persona identity, memory retrieval, Guardian policy, or long-term user context.

## 2. Core Design Principle

> Inference is plumbing. Cognition lives elsewhere.

Codexify should preserve strict separation between:

| Layer | Responsibility |
|---|---|
| Guardian | Permission, safety, orchestration policy |
| ContextBroker | Prompt cassette construction from memory, thread history, and context |
| ModelRouter / Inference Aggregation Layer | Provider selection and fallback |
| MLX Runner | Local model execution |
| Event Log / IDDB | Persistent memory and proof artifacts |
| Persona Layer | Interpretive style and identity lens |

The MLX Runner should receive a prompt and generation parameters, then return model output. It should not interpret persona authority or expand runtime permissions.

---

# 3. System Position

## 3.1 Existing Codexify Seam

Codexify already has the right architectural seam through the `ModelRouter` concept.

Current provider categories include:

```swift
enum ProviderType: String, Codable {
    case local
    case openai
    case claude
}
```

The `local` case is the intended integration point for local inference engines such as:

- MLX
- Ollama
- CoreML
- ONNX Runtime
- Custom local inference engines

The MLX Runner should plug into this `local` provider path rather than creating a separate inference kingdom.

## 3.2 Target Runtime Flow

```text
Codexify UI / Guardian / ContextBroker
        |
        v
Inference Aggregation Layer
        |
        v
MLXProviderAdapter
        |
        v
Local MLX Runner Daemon
        |
        v
Scheduler / Queue
        |
        v
MLX Worker Process
        |
        v
Loaded MLX Model
```

---

# 4. MVP Architecture

## 4.1 MVP Runtime Shape

For the first proof of concept:

```text
1 daemon
1 loaded model
1 active generation at a time
N queued requests
streaming responses
basic usage telemetry
```

This proves the contract without overloading the local machine or prematurely optimizing for multi-model concurrency.

## 4.2 Future Runtime Shape

Later:

```text
1 daemon
N registered models
M loaded hot models
controlled concurrent workers
fanout workflows
sequential chains
remote VaultNode routing
cloud fallback
```

The runner should be designed so this future shape does not require a rewrite.

---

# 5. Key Design Decision

## Concurrent API, Not Necessarily Concurrent GPU Execution

Codexify should support concurrent inference **as an orchestration capability**, but the runner should decide whether to execute jobs in parallel, serialize them, reject them, or route them elsewhere.

This matters because Apple Silicon uses unified memory, and model weights plus KV cache can quickly create memory pressure.

The safe principle:

```text
Codexify may submit multiple jobs concurrently.
The runner decides how many can execute safely.
```

The API should be concurrency-ready from day one, even if execution begins as serialized.

---

# 6. Core Components

## 6.1 MLXProviderAdapter

The adapter is the Codexify-facing provider implementation.

Conceptual interface:

```ts
interface ModelAdapter {
  generateCompletion(request: InferenceRequest): Promise<InferenceResponse>;
  streamCompletion(request: InferenceRequest): AsyncIterable<InferenceChunk>;
  supportsTools(): boolean;
  maxContextWindow(): number;
  costProfile(): CostProfile;
  healthCheck(): Promise<HealthStatus>;
}
```

Example MLX adapter:

```ts
class MLXProviderAdapter implements ModelAdapter {
  provider = "local.mlx";

  async generateCompletion(request: InferenceRequest) {
    return mlxRunnerClient.generate(request);
  }

  async streamCompletion(request: InferenceRequest) {
    return mlxRunnerClient.stream(request);
  }

  async healthCheck() {
    return mlxRunnerClient.health();
  }
}
```

The adapter allows Codexify to treat MLX as one provider among many.

## 6.2 MLX Runner Daemon

Recommended proof-of-concept form:

```text
Python FastAPI service
host: 127.0.0.1
port: 8765
service name: codexify-mlx-runner
```

Suggested startup command:

```bash
codexify-mlx-runner \
  --config ~/.codexify/mlx-runner/config.json \
  --host 127.0.0.1 \
  --port 8765
```

Why Python first:

- MLX and `mlx-lm` are easiest to integrate from Python.
- FastAPI gives simple HTTP and streaming support.
- Worker process control is straightforward.
- Codexify desktop, mobile, and server components can talk to it over localhost or LAN.

## 6.3 ModelRegistry

The registry describes available models without requiring all models to be loaded.

Example:

```json
{
  "models": [
    {
      "id": "qwen2.5-1.5b-instruct-mlx",
      "provider": "mlx",
      "path": "/Users/me/.codexify/models/qwen2.5-1.5b-instruct",
      "contextWindow": 32768,
      "defaultMaxTokens": 1024,
      "quantization": "4bit",
      "capabilities": ["chat", "streaming", "json"],
      "memoryClass": "small",
      "maxConcurrentJobs": 2
    }
  ]
}
```

Day one behavior:

```text
Registry supports many models.
Runtime loads one model.
Scheduler allows one active worker.
```

Future behavior:

```text
Registry supports many models.
Runtime keeps selected models hot.
Scheduler routes by model ID, capability, memory pressure, and priority.
```

## 6.4 Scheduler

The scheduler is the heart of the runner.

It owns:

- Job queueing.
- Cancellation.
- Timeout enforcement.
- Max concurrent jobs.
- Memory policy.
- Priority.
- Chain execution.
- Fanout execution.
- Per-model worker assignment.

Suggested statuses:

```ts
type JobStatus =
  | "queued"
  | "loading_model"
  | "running"
  | "streaming"
  | "completed"
  | "cancelled"
  | "failed"
  | "rejected";
```

Suggested job shape:

```ts
type InferenceJob = {
  id: string;
  mode: "single" | "fanout" | "chain";
  modelId: string;
  prompt: string;
  system?: string;
  temperature?: number;
  maxTokens?: number;
  contextBudgetTokens?: number;
  priority?: "low" | "normal" | "high";
  timeoutMs?: number;
  metadata?: {
    threadId?: string;
    shardId?: string;
    personaId?: string;
    taskType?: "chat" | "summarize" | "code" | "reflection" | "validator";
  };
};
```

Metadata may identify the upstream request context, but the runner should not interpret persona identity. Persona shaping happens before the request reaches the runner.

---

# 7. API Surface

## 7.1 Health Check

### `GET /health`

Response:

```json
{
  "ok": true,
  "runner": "codexify-mlx-runner",
  "version": "0.1.0",
  "activeModel": "qwen2.5-1.5b-instruct-mlx",
  "queueDepth": 0,
  "activeJobs": 1,
  "memory": {
    "pressure": "normal"
  }
}
```

## 7.2 Model Listing

### `GET /models`

Response:

```json
{
  "models": [
    {
      "id": "qwen2.5-1.5b-instruct-mlx",
      "loaded": true,
      "capabilities": ["chat", "streaming", "json"],
      "maxConcurrentJobs": 2
    }
  ]
}
```

## 7.3 Non-Streaming Generation

### `POST /v1/generate`

Request:

```json
{
  "modelId": "qwen2.5-1.5b-instruct-mlx",
  "input": {
    "system": "You are Codexify local assistant.",
    "prompt": "Summarize this memory fragment."
  },
  "params": {
    "temperature": 0.7,
    "maxTokens": 512
  },
  "metadata": {
    "taskType": "summarize",
    "threadId": "thread_123"
  }
}
```

Response:

```json
{
  "id": "job_abc",
  "modelId": "qwen2.5-1.5b-instruct-mlx",
  "output": "Summary text...",
  "usage": {
    "promptTokens": 210,
    "completionTokens": 94,
    "totalTokens": 304
  },
  "timing": {
    "queuedMs": 12,
    "inferenceMs": 1430
  }
}
```

## 7.4 Streaming Generation

### `POST /v1/stream`

Use Server-Sent Events or WebSocket.

Example SSE stream:

```text
event: token
data: {"text":"The"}

event: token
data: {"text":" runner"}

event: done
data: {"jobId":"job_abc"}
```

Use streaming for Codexify chat UI.

---

# 8. Advanced Execution Modes

## 8.1 Single Inference

Use for:

- Normal chat.
- Summarization.
- Local validator.
- Lightweight reflection.
- Cheap preprocessing.

Flow:

```text
Prompt -> Scheduler -> Worker -> Output
```

## 8.2 Fanout Inference

Use for:

- Persona council.
- Answer variants.
- Local debate.
- Uncertainty exploration.
- Validator/refiner patterns.

Flow:

```text
Prompt
  -> Variant A
  -> Variant B
  -> Variant C
  -> Collapse / Return
```

### `POST /v1/fanout`

Request:

```json
{
  "strategy": "same_model_variants",
  "modelId": "qwen2.5-1.5b-instruct-mlx",
  "prompt": "Design a memory scoring heuristic.",
  "variants": [
    {
      "id": "pragmatic",
      "temperature": 0.3,
      "systemSuffix": "Be concise and implementation-focused."
    },
    {
      "id": "critic",
      "temperature": 0.7,
      "systemSuffix": "Look for failure modes."
    },
    {
      "id": "synthesizer",
      "temperature": 0.5,
      "systemSuffix": "Integrate tradeoffs."
    }
  ],
  "collapse": {
    "mode": "return_all"
  }
}
```

Recommended MVP collapse modes:

```ts
type CollapseMode =
  | "return_all"
  | "longest"
  | "shortest"
  | "keyword_score"
  | "llm_summarize";
```

Start with:

```text
collapse.mode = "return_all"
```

Then add:

```text
collapse.mode = "llm_summarize"
```

Embedding-based collapse can come later.

## 8.3 Sequential Chain

Use for:

- Draft → critique → revise.
- Retrieve → summarize → answer.
- Code plan → risk scan → final plan.
- Guardian local preflight → model response → validator pass.

Flow:

```text
Step 1 output -> Step 2 input -> Step 3 input -> Final
```

### `POST /v1/chain`

Request:

```json
{
  "steps": [
    {
      "id": "draft",
      "modelId": "small-chat",
      "prompt": "Draft a response."
    },
    {
      "id": "critic",
      "modelId": "small-chat",
      "promptTemplate": "Critique this draft:\n\n{{draft.output}}"
    },
    {
      "id": "final",
      "modelId": "small-chat",
      "promptTemplate": "Revise using critique:\n\nDraft:\n{{draft.output}}\n\nCritique:\n{{critic.output}}"
    }
  ]
}
```

---

# 9. Concurrency Policy

Concurrency should be controlled explicitly.

Example config:

```json
{
  "concurrency": {
    "globalMaxActiveJobs": 2,
    "perModelMaxActiveJobs": 1,
    "maxQueueDepth": 32,
    "defaultTimeoutMs": 60000,
    "allowParallelSameModel": false,
    "allowParallelDifferentModels": false
  }
}
```

## 9.1 MVP Policy

```text
globalMaxActiveJobs = 1
perModelMaxActiveJobs = 1
allowParallelSameModel = false
allowParallelDifferentModels = false
```

Fanout can still accept multiple variants, but the scheduler may serialize them.

This proves the orchestration contract before stressing Metal or unified memory.

## 9.2 Experimental Policy

For small models only:

```text
globalMaxActiveJobs = 2
perModelMaxActiveJobs = 2
allowParallelSameModel = true
```

## 9.3 Future Policy

Later, concurrency can be based on:

- Model memory class.
- Available unified memory.
- Thermal state.
- KV cache pressure.
- Task priority.
- Whether the model is local, remote, or cloud-based.

---

# 10. Codexify Integration

## 10.1 Swift / Mobile Integration

Existing local placeholder:

```swift
private func runLocalModel(input: String) async throws -> String {
    // TODO: Integrate with local model engine
    throw ModelRouterError.localModelNotImplemented
}
```

Target replacement:

```swift
private func runLocalModel(input: String) async throws -> String {
    return try await MLXRunnerClient.shared.generate(input: input)
}
```

For iOS, do not require MLX on-device first.

Preferred route:

```text
iPhone Scout
  -> local provider
    -> on-device model later
    -> or VaultNode / Mac runner over LAN
```

## 10.2 ContextBroker Integration

The ContextBroker should remain responsible for building the prompt cassette.

Flow:

```text
ContextBroker.buildContext()
  -> context.formatForPrompt()
  -> ModelRouter.routeRequest()
  -> MLXProviderAdapter
  -> mlx-runner
```

The runner should not perform RAG. It receives already-assembled context.

---

# 11. Suggested File Layout

## 11.1 Codexify Repository

```text
packages/
  inference/
    src/
      adapters/
        MLXProviderAdapter.ts
      types/
        InferenceRequest.ts
        InferenceResponse.ts
      clients/
        MLXRunnerClient.ts

services/
  mlx-runner/
    pyproject.toml
    README.md
    codexify_mlx_runner/
      main.py
      config.py
      registry.py
      scheduler.py
      worker.py
      mlx_backend.py
      schemas.py
      telemetry.py
      collapse.py
    tests/
      test_scheduler.py
      test_registry.py
      test_api.py

.codexify/
  mlx-runner.config.json
```

## 11.2 iOS / Swift Side

```text
ios/Codexify/Sources/
  MLXRunnerClient.swift
  ModelRouter.swift
```

---

# 12. MVP Definition of Done

The proof of concept is complete when:

```text
1. Start local MLX runner daemon.
2. Load one configured MLX model.
3. Codexify can call /health.
4. Codexify can call /models.
5. Codexify can submit one non-streaming prompt.
6. Codexify can stream tokens from one prompt.
7. Multiple requests queue safely.
8. Fanout endpoint accepts 3 variants and returns 3 outputs.
9. Chain endpoint executes draft -> critique -> final.
10. Usage telemetry logs:
    - modelId
    - latency
    - queued time
    - prompt token estimate
    - completion token estimate
    - error status
    - cancellation status
11. ModelRouter.local no longer throws localModelNotImplemented.
```

---

# 13. Implementation Phases

## Phase 0: Runner Skeleton

Build:

- FastAPI app.
- `/health`.
- `/models`.
- Config loader.
- Mock worker returning fake text.

Goal:

```text
Prove daemon lifecycle and Codexify client connection.
```

## Phase 1: Real MLX Single Model

Build:

- MLX model loader.
- `/v1/generate`.
- Request timeout.
- Basic queue.

Goal:

```text
Prove local generation through MLX.
```

## Phase 2: Streaming

Build:

- `/v1/stream`.
- Token streaming through SSE or WebSocket.
- Codexify chat UI integration.

Goal:

```text
Make local inference feel native inside Codexify.
```

## Phase 3: Fanout

Build:

- `/v1/fanout`.
- Multiple prompt variants.
- Serialized or controlled parallel execution.
- `return_all` collapse mode.

Goal:

```text
Prove concurrent workflow semantics without requiring true concurrent GPU execution.
```

## Phase 4: Chain

Build:

- `/v1/chain`.
- Step output templating.
- Sequential execution.
- Failure handling per step.

Goal:

```text
Enable local draft -> critique -> revise and validator/refiner workflows.
```

## Phase 5: Codexify Provider Adapter

Build:

- `MLXProviderAdapter`.
- `MLXRunnerClient`.
- Local provider config.
- Swift or desktop local route integration.

Goal:

```text
Make MLX a first-class Codexify provider.
```

---

# 14. Telemetry

Track per request:

```text
jobId
modelId
mode
status
queuedMs
inferenceMs
promptTokens
completionTokens
totalTokens
retryCount
errorCode
cancelled
createdAt
completedAt
```

Telemetry should be internal and local-first. It should support debugging, performance analysis, and future routing decisions.

---

# 15. Failure Modes and Mitigations

## 15.1 Memory Pressure

Risk:

```text
Large model + long context + multiple active jobs can exhaust unified memory.
```

Mitigation:

```text
Scheduler enforces max active jobs.
Context budget is explicit.
Model registry includes memoryClass.
Future memory pressure checks can throttle or reject jobs.
```

## 15.2 KV Cache Growth

Risk:

```text
Long contexts increase RAM usage and degrade performance.
```

Mitigation:

```text
Expose contextBudgetTokens.
Support memory modes:
  - stateless
  - balanced
  - long
```

## 15.3 Runaway Local Workflows

Risk:

```text
Fanout and chain modes can accidentally create expensive local loops.
```

Mitigation:

```text
Max chain steps.
Max fanout variants.
Timeout per job.
Timeout per workflow.
Cancellation API.
No recursive autonomous loops.
```

## 15.4 Persona Authority Leakage

Risk:

```text
A persona prompt could be treated as runtime authority.
```

Mitigation:

```text
Runner does not interpret persona metadata.
Execution authority belongs to Guardian / permission layer.
Runner only executes bounded inference jobs.
```

## 15.5 Provider Lock-In

Risk:

```text
Hardcoding MLX paths into Codexify runtime would make future providers harder.
```

Mitigation:

```text
Use provider adapter pattern.
Keep MLX behind local.mlx provider ID.
Preserve routing through ModelRouter / IAL.
```

---

# 16. Future Extensions

## 16.1 Remote VaultNode Runner

Future topology:

```text
Codexify Client
  -> Local ModelRouter
    -> local.mlx on MacBook
    -> vault.mlx on Mac Mini
    -> ollama.local
    -> cloud provider fallback
```

This allows the MacBook or iPhone to treat a Mac Mini as a private inference node.

## 16.2 Multi-Model Hot Loading

Future scheduler can keep multiple models loaded when memory allows:

```text
small-chat
small-validator
coder-model
summarizer-model
```

## 16.3 Model Role Routing

Example roles:

```text
conversation -> small-chat
coding -> qwen-coder
reflection -> small-reflector
validator -> tiny-validator
summarization -> compact-summarizer
```

## 16.4 WhisperMesh Compatibility

The runner should eventually support a private model fabric:

```text
Dedicated Compute Node
Dedicated Conversational Node
Dedicated Memory Node
Unified API Layer
Clients as Thin Terminals
```

The MLX Runner can become one execution node inside that fabric.

---

# 17. Recommended Internal Names

## Component Names

```text
Codexify Local Inference Runner
Codexify MLX Runner
codexify-mlx-runner
local.mlx
MLXProviderAdapter
MLXRunnerClient
```

## Avoid

```text
PersonaRunner
GuardianRunner
MemoryRunner
```

Those names blur boundaries. The runner is inference infrastructure, not cognition or identity.

---

# 18. Design Mantra

```text
Guardian decides whether inference is allowed.
ContextBroker builds the prompt cassette.
ProviderRouter selects the provider.
MLX Runner executes the generation.
Event log stores the proof.
Persona never silently gains execution authority.
```

---

# 19. Recommendation

Build the MLX Runner as a **local daemon plus provider adapter**, not as an embedded-only library.

This gives Codexify:

- Local desktop inference now.
- Mobile-to-VaultNode inference later.
- Parallel workflow experiments.
- Model hot-swapping later.
- Remote Mac Mini support.
- WhisperMesh node routing later.
- Cloud fallback through the same provider abstraction.
- No forced rewrite when adding Ollama, llama.cpp, CoreML, ONNX, or remote worker nodes.

The first version should be boring in the best way:

```text
One runner.
One model.
One queue.
One adapter.
One clean seam.
```

Then the cathedral can grow ribs.

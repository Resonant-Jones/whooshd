# ThreadWake Cache

### Persistent KV Context Infrastructure for Codexify

*Spec v0.1 — Sovereign Local Inference Persistence Layer*

---

## 1. Purpose

ThreadWake Cache is a persistent KV-cache orchestration layer for Codexify designed to make local inference feel cloud-native.

Its purpose is to:

* eliminate repeated prefill costs
* persist reusable cognitive state across sessions
* dramatically reduce TTFT (time to first token)
* preserve long-context workflows after restart/crash
* enable stable agentic coding and persistent conversational systems

The system is inspired by:

* oMLX SSD-backed KV persistence ([GitHub][1])
* prefix caching research ([arXiv][2])
* continuous batching runtimes on Apple Silicon ([oMLX][3])

---

# 2. Core Philosophy

ThreadWake is not:

* a benchmark gimmick
* raw “cache everything”
* blind SSD dumping

ThreadWake *is*:

* a selective semantic persistence layer
* a stability-aware inference substrate
* a sovereign local cognition accelerator

The goal is to make Codexify feel like:

> “the system remembers where thought left off.”

---

# 3. System Architecture

```text
                ┌─────────────────────┐
                │   Codexify Thread   │
                └─────────┬───────────┘
                          │
                Canonical Context Build
                          │
                          ▼
                ┌─────────────────────┐
                │ Context Fingerprint │
                └─────────┬───────────┘
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   RAM Hot KV Cache            SSD Warm KV Cache
      (fast tier)                 (persistent tier)
            │                           │
            └─────────────┬─────────────┘
                          ▼
                Inference Runtime
          (MLX / Ollama / llama.cpp)
```

---

# 4. Design Goals

| Goal                  | Description                                       |
| --------------------- | ------------------------------------------------- |
| Rapid restart         | Resume threads instantly after app/server restart |
| Stable persistence    | Avoid catastrophic memory crashes                 |
| Prefix reuse          | Reuse stable prompt regions                       |
| Agent optimization    | Accelerate coding and orchestration loops         |
| SSD safety            | Prevent destructive write amplification           |
| Runtime abstraction   | Work across multiple backends                     |
| Sovereignty-first     | Local-only by default                             |
| Incremental evolution | Future-compatible with distributed memory systems |

---

# 5. Key Concepts

## 5.1 Context Fingerprint

Every cacheable context receives a deterministic hash:

```text
fingerprint =
  hash(
    model_id +
    tokenizer_hash +
    persona_hash +
    system_prompt_hash +
    stable_prefix_hash +
    context_pack_version
  )
```

This becomes the canonical cache identity.

---

## 5.2 Stable Prefix Regions

Only cache:

* persona core
* pinned project docs
* long-lived thread memory
* retrieved RAG fragments
* stable agent instructions

Avoid caching:

* volatile user tail prompts
* rapidly mutating scratchpads
* streaming intermediate generations

This dramatically reduces:

* cache invalidation
* SSD churn
* unnecessary recomputation

---

# 6. Cache Tiers

## Tier 1 — RAM Hot Cache

Purpose:

* immediate reuse
* active thread acceleration

Characteristics:

* fastest access
* LRU managed
* volatile
* low latency

---

## Tier 2 — SSD Warm Cache

Purpose:

* persistence across restart
* large-context continuity

Characteristics:

* persisted in safetensors or mmap blocks
* block-addressable
* external NVMe recommended
* survives runtime restarts

Inspired by oMLX hot/cold KV architecture. ([oMLX][3])

---

# 7. Storage Layout

```text
Codexify/
 └── threadwake/
      ├── metadata.db
      ├── cache/
      │    ├── model_qwen3/
      │    │     ├── fingerprint_x/
      │    │     │     ├── block_00001.st
      │    │     │     ├── block_00002.st
      │    │     │     └── meta.json
      │    └── model_llama/
      └── temp/
```

---

# 8. Metadata Schema

## `threadwake_cache_entries`

| Field           | Type        |
| --------------- | ----------- |
| id              | UUID        |
| fingerprint     | TEXT UNIQUE |
| model_id        | TEXT        |
| tokenizer_hash  | TEXT        |
| persona_id      | UUID        |
| thread_id       | UUID        |
| context_tokens  | INTEGER     |
| kv_precision    | TEXT        |
| cache_path      | TEXT        |
| size_bytes      | BIGINT      |
| created_at      | TIMESTAMP   |
| last_used_at    | TIMESTAMP   |
| access_count    | INTEGER     |
| storage_tier    | TEXT        |
| runtime_backend | TEXT        |
| valid           | BOOLEAN     |

---

# 9. Cache Lifecycle

## Creation

Cache is promoted only when:

* prefix exceeds token threshold
* thread reused multiple times
* agent session marked persistent
* context stable enough

---

## Promotion Rules

```text
RAM → SSD promotion occurs when:
- repeated reuse detected
- memory pressure increases
- long-context session identified
```

---

## Eviction

Weighted LRU:

* access frequency
* token cost saved
* recency
* storage pressure
* persona priority

---

# 10. Runtime Abstraction Layer

ThreadWake must NOT couple directly to MLX.

Instead:

```text
ThreadWakeRuntimeAdapter
 ├── MLXAdapter
 ├── OllamaAdapter
 ├── llama.cpp Adapter
 ├── vLLM Adapter (future)
 └── Remote Cloud Adapter (future)
```

This protects Codexify from runtime churn.

---

# 11. Apple Silicon Optimization

## Recommended Strategies

### KV Quantization

Use:

* q8
* q4
* TurboQuant-style approaches

KV compression can reduce memory massively. ([GitHub][4])

---

## Continuous Batching

Future ThreadWake integration should support:

* shared prefix batching
* multi-agent scheduling
* cooperative decode windows

Inspired by:

* oMLX batching ([oMLX][3])

---

# 12. SSD Safety System

Critical.

The system must prevent:

* excessive write amplification
* internal Mac SSD degradation

## Rules

### Default Warning

```text
"Internal SSD caching may increase wear.
External NVMe recommended for heavy workloads."
```

---

## Write Governance

ThreadWake should:

* debounce writes
* batch block flushes
* avoid rewriting identical blocks
* compress cold blocks
* track SSD throughput metrics

---

## Recommended Modes

| Mode         | Behavior                            |
| ------------ | ----------------------------------- |
| Conservative | RAM-only unless explicitly promoted |
| Balanced     | SSD persistence for stable threads  |
| Aggressive   | Maximum persistence and reuse       |

Default:

> Balanced

---

# 13. Failure Recovery

ThreadWake must survive:

* runtime crashes
* kernel panics
* partial writes
* corrupted cache blocks

## Mechanisms

* atomic metadata commits
* append-only journaling
* cache validation hashes
* lazy corruption detection
* block quarantine

---

# 14. Agentic Workflow Optimization

ThreadWake is especially valuable for:

* coding agents
* MCP tool loops
* large repo RAG
* iterative editing
* persistent companions

Reason:
Most latency comes from repeated prefill, not decode. ([Vijay Kodam][5])

---

# 15. UI Integration

## Thread Indicators

```text
● Cold
◐ Warm
◉ Hot
```

---

## Metrics

Display:

* TTFT saved
* cache hit ratio
* SSD usage
* RAM usage
* estimated tokens reused

---

## Controls

Per-thread:

* Pin cache
* Flush cache
* Persist thread
* External disk selection

---

# 16. Security

## Default Model

* fully local
* encrypted optional
* no telemetry
* no cloud dependency

---

## Future

Potential encrypted KV persistence:

* per-user keys
* persona-scoped caches
* project-isolated storage

---

# 17. Future Evolution

## Planned Extensions

### Semantic Prefix Matching

Reuse partially matching prefixes.

### Distributed ThreadWake

Cross-device cache sync:

* VaultNode ↔ Scout
* LAN inference clusters
* WhisperMesh memory routing

### Position-Independent Caching

Inspired by MPIC research. ([arXiv][2])

### Predictive Prefetch

Prewarm likely next contexts.

### Persona Wake States

Persistent persona cores always partially warm.

---

# 18. Recommended MVP Scope

## Phase 1

Implement:

* deterministic fingerprinting
* RAM hot cache
* SSD warm persistence
* MLX adapter
* metadata DB
* manual flush/pin
* stable-prefix caching only

Do NOT initially implement:

* distributed sync
* semantic reuse
* predictive warming
* remote cache federation

---

# 19. Success Criteria

ThreadWake succeeds if:

| Metric                | Target                    |
| --------------------- | ------------------------- |
| Restart recovery      | <5s warm TTFT             |
| Repeated prompt reuse | 80–95% prefill reduction  |
| Crash resilience      | No metadata corruption    |
| SSD write reduction   | >50% vs naive persistence |
| User perception       | “Local feels cloud-fast”  |

---

# 20. Canonical Internal Framing

> “ThreadWake is a persistence substrate for cognition-in-progress.”

Not:

* merely cache
* merely optimization
* merely inference acceleration

It is:

* continuity infrastructure.

And that distinction changes how the whole system feels.

[1]: https://github.com/jundot/omlx?utm_source=chatgpt.com "GitHub - jundot/omlx: LLM inference server with continuous batching ..."
[2]: https://arxiv.org/abs/2502.01960?utm_source=chatgpt.com "MPIC: Position-Independent Multimodal Context Caching System for Efficient MLLM Serving"
[3]: https://omlx.ai/?utm_source=chatgpt.com "oMLX — LLM inference, optimized for your Mac"
[4]: https://github.com/helgklaizar/turboquant-mlx?utm_source=chatgpt.com "GitHub - helgklaizar/turboquant-mlx: TurboQuant MLX: Flagship high ..."
[5]: https://vijay.eu/co-authored/llm-inference-internals-apple-silicon/?utm_source=chatgpt.com "LLM Inference Internals: KV Cache, Flash Attention, and Optimizing for ..."

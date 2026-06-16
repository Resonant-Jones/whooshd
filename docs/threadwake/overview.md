# ThreadWake Cache — Overview

ThreadWake is a **runtime optimization** that reuses compatible pre-computed
prompt-prefix state across chat requests.  It improves latency for repeated
long-context workflows — personas, tools, project context, thread
continuation — by avoiding redundant computation of identical prompt prefixes.

ThreadWake is **not** AI memory.  It does not persist across restarts, does
not store raw prompt content, and does not imply the model "remembers" you.

---

## What ThreadWake Is

- A **prefix-reuse cache** for the prompt-processing (prefill) phase of
  inference.
- **Scope-enforced**: cached state is only reused within the same thread,
  user, or project boundary — cross-scope reuse is prevented by design.
- **Deterministic**: only exact content-hash matches trigger reuse.  No
  fuzzy matching, no approximate retrieval.
- **Observable**: all modes produce metrics (hit rates, token counts,
  memory estimates) even when KV reuse is disabled.
- **Optional**: defaults to off.  Must be explicitly enabled.

## What ThreadWake Is Not

- **Not human memory.**  It does not persist between restarts, does not
  learn from conversations, and does not synthesize new knowledge.
- **Not identity.**  It does not track or model who you are.
- **Not persistent storage.**  All cached state lives in process memory
  and is lost when Whoosh'd restarts.
- **Not a retrieval system.**  It only reuses exact prompt-prefix matches.
  It does not search, rank, or retrieve similar prefixes.
- **Not always faster.**  For short prompts or models with very fast
  prefill, the overhead of cache management may exceed the benefit.

---

## How It Works

```
Request:  [ System Prompt | Persona | Tools | Project | History | User ]
           └─────────────── Stable Prefix ──────────────┘└── Dynamic ──┘

Step 1 — Compile: Segment the prompt into stable (system, persona, tools,
          project, prior history) and dynamic (latest user message, tool
          outputs) layers.  Each segment is hashed with SHA-256.

Step 2 — Key: Build a deterministic cache key from the stable prefix hash,
          model identifier, tokenizer hash, chat template hash, and scope.

Step 3 — Lookup: Check the in-memory metadata index.  If a ready entry
          exists for the exact cache key and scope context — cache hit.

Step 4a — Hit: Clone the stored KV handle, skip the prefill phase for
          stable tokens, and generate only from the dynamic tail.

Step 4b — Miss: Run full prefill + generation.  After completion, store the
           stable prefix KV handle and mark the entry ready.

Step 5 — Scope enforcement: Every lookup validates the scope context
          (thread ID, user ID, project ID).  Thread A's cache entries are
          never retrieved by Thread B.
```

---

## Modes

| Mode | Behavior | Cache Reuse | Metrics |
|---|---|---|---|
| `off` | ThreadWake disabled | None | No |
| `observe` | Hash, segment, report — no KV reuse | No | Yes |
| `ephemeral` | Full KV reuse for exact prefix matches | Yes | Yes |
| `session` | Ephemeral + monotonic conversation continuation | Yes | Yes |
| `advanced` | Reserved for future use | — | — |

### `off`
Default.  No ThreadWake processing occurs.  Zero overhead.

### `observe`
ThreadWake compiles prompt graphs, computes hashes, evaluates eligibility,
and records metrics — but does **not** store or reuse KV cache state.
Useful for measuring potential benefit before enabling reuse.

### `ephemeral`
Full KV reuse for **exact** stable-prefix matches.  The first request with
a given prefix computes and caches the prefill.  Subsequent identical
prefixes skip the prefill phase entirely.

### `session`
Extends ephemeral mode with **monotonic conversation continuation**.  If a
thread's messages grow monotonically (append-only, no edits), each new
request resumes from the previous turn's KV state and only prefill the new
messages.  Edits or truncations invalidate the continuation chain.

### `advanced`
Reserved for future modes (branching continuation, priority eviction tiers).

---

## When ThreadWake Helps

- **Long stable prefixes**: System prompts, personas, tool manifests, and
  project context that span hundreds or thousands of tokens benefit most.
- **Repeated identical prefixes**: Workflows that submit the same prefix
  across many turns (e.g., Codexify with Guardian + persona + tools).
- **Monotonic conversations**: Session mode accelerates multi-turn
  conversations where only new messages are appended.
- **Models with slower prefill**: Larger models or CPU-bound backends
  benefit more from prefill skipping than small GPU models.

## When ThreadWake Does Not Help

- **Short prompts**: A 50-token user message has negligible prefill cost.
- **Every message is unique**: If no two requests share a stable prefix,
  the cache is never hit.
- **Frequently edited history**: Session mode invalidates on edits.
- **Hardware with very fast prefill**: Apple Silicon with small models
  may prefill faster than the cache management overhead.

---

## Scope

ThreadWake enforces scope boundaries to prevent accidental cross-context
reuse:

| Scope | Reuse Boundary | Default? |
|---|---|---|
| `request` | Same request only | No |
| `thread` | Same thread ID | **Yes** |
| `project` | Same project ID | No |
| `user` | Same user ID | No |
| `global` | All requests (disabled by default) | No |

Global scope requires explicit opt-in via `WHOOSHD_THREADWAKE_ALLOW_GLOBAL=true`
and is **disabled by default** for safety.

---

## Lifecycle

- **Startup**: ThreadWake initializes an empty in-memory metadata index.
  No state is loaded from disk.
- **Request**: Each eligible request is compiled into a prompt graph,
  hashed, and checked against the index.
- **Eviction**: Least-recently-used entries are evicted when the index
  exceeds `max_entries` or estimated memory exceeds `max_memory_mb`.
- **Flush**: The index can be flushed via the admin endpoint or API.
- **Shutdown**: All cached state is discarded.  No state survives a restart.

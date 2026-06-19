# Backend Snapshot Feasibility Investigation

**Phase**: M19  
**Date**: 2026-06-17  
**Branch**: `feature/threadwake-m19-backend-feasibility`  
**Status**: Documentation-first investigation — no implementation

---

## Summary Recommendation

**No backend is ready for production snapshot materialization.** MLX in-process
is the closest candidate with a viable path (tokenizer access + `mx.save`),
but lacks a stable public KV-extraction API. All other backends are
opaque (HTTP/subprocess) or blocked. Proceed to M20 only after at least one
backend exposes a documented, versioned, identity-bound snapshot API.

---

## Investigation Scope

Evaluated every Whoosh'd backend path against the requirements for a
`SnapshotMaterializer`:

1. **Identity binding** — can the snapshot be cryptographically bound to
   the exact model, tokenizer, chat template, and quantization that
   produced it?
2. **Safe serialization** — can KV state be extracted without inspecting
   private internals, using pickle, or depending on undocumented APIs?
3. **Restore safety** — can a serialized snapshot be restored without
   corrupting model state, crashing the runtime, or producing subtly
   wrong output?
4. **Public API stability** — does the backend expose a documented,
   versioned API for snapshot creation and restore?

---

## Current ThreadWake Materialization Layer (M13 on main)

ThreadWake currently has through Phase M13 on the `main` branch:

| Module | Purpose | Status |
|---|---|---|
| `policy.py` | `SnapshotPolicyEngine` — deterministic eligibility evaluation (min seen count, score, ratio, age) | Production |
| `snapshot_manifest.py` | `SnapshotManifest` + `SnapshotManifestBuilder` — sanitized metadata manifests for eligible candidates | Production |
| `storage.py` | `SQLiteThreadWakeStorage` — optional persistence for candidates and snapshot manifests | Production |
| `prefix_proof.py` | `StablePrefixProofEngine` — exact token-prefix comparison | Production |
| `candidate_selection.py` | `SnapshotCandidateSelector` — scores and selects hypothetical candidates | Production |
| `replay_analysis.py` | `CandidateReplayAnalyzer` — ranks repeated high-value candidates | Production |

**Not yet on main** (developed on `feature/threadwake-real-tokenization` beyond M13,
pending future merge): artifact registry, snapshot creation gate, material
contract, material validation, backend materialization interface.

---

## Backend Verdict Table

| Backend | Verdict | Rationale |
|---|---|---|
| **MLX in-process** (`mlx.py`) | `REQUIRES_UPSTREAM_API` | Has tokenizer access via `mlx_lm.load()`, has `mx.save`/`mx.load` for individual arrays. No stable public API for KV cache extraction. Cache structure is version-dependent and undocumented. Identity binding possible through model/tokenizer hashes. |
| **MLX-LM Server** (subprocess) | `BLOCKED_OPAQUE_BACKEND` | No direct tokenizer or model access. Prompt rendering and tokenization happen inside the subprocess. KV state is inaccessible from Whoosh'd. |
| **MLX-VLM** (subprocess) | `BLOCKED_OPAQUE_BACKEND` | Same as MLX-LM Server, plus multimodal complexity (image token interleaving). |
| **llama.cpp HTTP/server** | `BLOCKED_OPAQUE_BACKEND` | HTTP-forwarded requests. Tokenization and KV management are server-side and opaque. Slot save/restore API exists but is undocumented and fragile. |
| **llama.cpp C/API** (future) | `REQUIRES_UPSTREAM_API` | `llama_state_save_file`/`llama_state_load_file` exist in the C API. Requires exact model binary match, identical context parameters. Not currently accessible from Whoosh'd's HTTP-only adapter. Future research only. |
| **Forwarding / external routes** | `NOT_APPLICABLE` | External server tokenization and KV state are untrusted by definition. No identity binding possible. |
| **Stub / Fake** (test) | `TEST_ONLY` | `FakeKVBackend` in tests only. Not a real backend. |
| **MLX in-process (hypothetical future)** | `EXPERIMENTAL_WITH_PUBLIC_API` | If and when `mlx-lm` exposes a documented `save_kv_cache(model, tokenizer, tokens)` / `load_kv_cache(model, path)` API with versioning and identity binding. This does not exist today. |

---

## Detailed Backend Analysis

### MLX In-Process (`mlx.py`)

**Current state**: The adapter has direct access to the tokenizer (`self._tokenizer`)
and model (`self._model`) via `mlx_lm.load()`. Prompt rendering uses
`apply_chat_template(tokenize=False)`. The tokenizer is passed to
`mlx_lm.generate()`.

**What exists**:
- `mx.save(array, file)` — saves individual arrays to `.npy` format
- `mx.load(file)` — loads arrays
- Tokenizer identity via `name_or_path` + `vocab_size`
- Chat template identity via `chat_template` string hash

**What's missing**:
- No `save_kv_cache()` or equivalent public API in `mlx-lm`
- KV cache is stored as a list of `(key, value)` tuple pairs per layer —
  internal structure, version-dependent, undocumented
- No versioned snapshot format
- No identity binding (model architecture hash is not exposed)

**Required for SUPPORTED status**:
1. `mlx-lm` exposes a documented `save_kv_cache(path)` method
2. Snapshot includes model architecture hash, layer count, head dim, dtype
3. `load_kv_cache(path)` validates identity before restoring
4. Version field in snapshot format for forward compatibility
5. Public test demonstrating round-trip fidelity

**Current verdict**: `REQUIRES_UPSTREAM_API`

### MLX-LM Server (Subprocess)

The adapter manages `mlx_lm.server` as a subprocess and forwards HTTP requests
to it. Whoosh'd has no direct access to the tokenizer, model, or KV cache.

**Verdict**: `BLOCKED_OPAQUE_BACKEND` — no path to materialization without
architectural change (e.g., the server exposing a snapshot endpoint).

### llama.cpp HTTP/Server

The adapter forwards requests to a llama.cpp server's `/v1/chat/completions`
endpoint. All tokenization and KV management are server-side.

The server has an undocumented slot save/restore feature, but:
- Not accessible from Whoosh'd's current HTTP adapter
- Not versioned or documented as a stable contract
- Requires exact model binary match

**Verdict**: `BLOCKED_OPAQUE_BACKEND`

### llama.cpp C/API (Future Research)

The `llama.cpp` C library exposes `llama_state_save_file()` and
`llama_state_load_file()`. These could theoretically be used if Whoosh'd
linked against the C library directly. This is not currently possible
from the Python HTTP adapter.

**Verdict**: `REQUIRES_UPSTREAM_API` — deferred to future research.

---

## Identity Binding Requirements

Any future snapshot-capable backend MUST provide:

| Binding | Mechanism |
|---|---|
| Model identity | SHA-256 of model weights file or architecture fingerprint |
| Tokenizer identity | SHA-256 of tokenizer `name_or_path:vocab_size` |
| Chat template identity | SHA-256 of `chat_template` string |
| Quantization identity | Quantization label (e.g., `4bit`, `8bit`) |
| Backend version | `mlx-lm` version string or `llama.cpp` commit hash |
| Context window | `--ctx-size` parameter |

A snapshot MUST be invalidated if any binding changes. This is currently
enforced at the ThreadWake metadata level (prefix hash, tokenizer hash,
chat template hash) but not at the backend KV level.

---

## Safety Requirements

Any future snapshot materialization MUST:

1. **Never use pickle** — pickle can execute arbitrary code and is not
   safe for untrusted snapshot files
2. **Validate identity before restore** — check all bindings before
   loading KV tensors
3. **Use atomic writes** — write to temp file, rename to final path
4. **Include integrity check** — HMAC or checksum over the serialized
   payload
5. **Encrypt at rest** — if snapshots contain KV tensors (which are
   a lossy transform of input tokens), they must be treated as
   sensitive user data
6. **Auto-expire** — snapshots unused for a configurable TTL must be
   automatically deleted
7. **Opt-in only** — gated behind an explicit configuration flag,
   disabled by default

---

## Test Results

Existing ThreadWake tests on `main`:

```
597 passed, 4 skipped in ~2s
```

No M19 code changes — tests run to confirm no regression from the
documentation addition.

---

## Recommendation for M20

**Hold on backend materialization implementation.**

Instead, M20 should focus on merging the remaining ThreadWake infrastructure
from `feature/threadwake-real-tokenization` (M14–M18: artifact registry,
snapshot creation gate, material contract, material validation, backend
materialization interface) to `main`. These phases add the metadata
infrastructure that backends will target, without enabling any backend
to actually materialize snapshots.

Once merged:
1. The full snapshot metadata pipeline (manifest → artifact → material
   contract → validation) is available on `main`
2. Backends can register their `SnapshotMaterializer` capability
   (all reporting UNSUPPORTED or DECLARED)
3. Future backend API changes (e.g., `mlx-lm` adding KV save/load) can
   be adopted incrementally

**M20 title**: `feat(threadwake): merge snapshot infrastructure (M14-M18)` or
`chore(threadwake): promote remaining snapshot metadata pipeline to main`

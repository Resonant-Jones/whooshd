# Durable KV Cache Snapshots — Research Spike

**Status**: Research / Recommendation  
**Date**: 2026-06-15  
**Author**: ThreadWake Cache Phase K

---

## Summary Recommendation

**DEFER.** Do not implement durable KV cache snapshots in the current release
cycle. The risks (version-skew corruption, silent semantic drift, accidental
cross-user exposure, disk-bloat) outweigh the expected benefit for a
single-user local-first inference broker.  Re-evaluate when at least one
backend exposes a supported, versioned, cryptographically-verified KV
serialization primitive.

---

## Backend Support Matrix

| Backend | KV Serialization API | Safe Restore? | Metadata Required | Maturity |
|---|---|---|---|---|
| **MLX (mlx-lm, in-process)** | `mx.save` / `mx.load` per-array (.npy) | No — cache is a nested list of tuples per layer; no framework-level cache save/restore | Model architecture hash, layer count, head dim, context length, dtype | Experimental |
| **MLX-LM Server (subprocess)** | None | — | — | None |
| **MLX-VLM (subprocess)** | None | — | — | None |
| **llama.cpp (HTTP server)** | Slot save/restore via undocumented API | Partially — server must not be reloading; model must be identical | Slot ID, model fingerprint, tokenizer hash, prompt token IDs | Fragile |
| **llama.cpp (C API, future)** | `llama_state_load_file` / `llama_state_save_file` | Requires exact same model binary, tokenizer, and context params | Model hash, context size, batch size | Moderate |
| **Stub (test)** | N/A | N/A | N/A | N/A |

### Detailed Backend Analysis

#### MLX (in-process via `mlx-lm`)

The KV cache in `mlx-lm` is implemented as a list of `(key, value)` tuple pairs
per transformer layer, stored as `mx.array` objects on Apple Silicon's unified
memory via Metal.

- `mx.save(arr, file)` can persist individual arrays to `.npy` format
- `mx.load(file)` can restore them
- **No higher-level KV cache serialization exists** in `mlx-lm` or MLX core
- Custom serialization would require:
  - Walking `model.layers[i].attention.kv_cache` (internal structure)
  - Saving each `(k, v)` tensor pair per layer
  - Recording metadata: model architecture hash, layer count, head dimension,
    hidden dimension, dtype, sequence length
- **Restore risk**: MLX's internal cache layout is version-dependent and may
  change between `mlx-lm` releases without notice. A snapshot from version
  N may silently produce garbage output on version N+1.

#### llama.cpp (HTTP Server)

llama.cpp's server exposes `/slots/{id}` endpoints that allow saving and
restoring slot state (including KV cache). The underlying C API provides
`llama_state_save_file` and `llama_state_load_file`.

- **Prerequisites**: exact same model file, identical tokenizer, identical
  `--ctx-size` parameter
- **Failure modes**: model hash mismatch → crash or garbage; context size
  mismatch → truncation or out-of-bounds access
- **Disk size**: roughly `2 × layers × head_dim × n_heads × ctx_len × dtype_size`
  — for a 7B model with 32 layers, 128 heads, 128 head_dim, 4096 context,
  fp16: ~32 × 128 × 128 × 4096 × 2 × 2 bytes ≈ 8 GiB per full snapshot
- The HTTP server's slot API is not formally documented as a stable contract
  and may change between llama.cpp releases

---

## Security / Privacy Analysis

### Threat Model

| Threat | Severity | Mitigation |
|---|---|---|
| Snapshot contains reconstructable prompt content | **Critical** | KV cache is a lossy transform of input tokens; theoretical reconstruction attacks exist for some architectures. Snapshot must be treated as **sensitive user data**. |
| Cross-user snapshot reuse | **Critical** | Snapshots must be bound to user + model + tokenizer identity via cryptographic binding. Currently Whoosh'd has no multi-user model. |
| Snapshot poisoning | **High** | A corrupted or maliciously-modified snapshot file could cause undefined model behavior. Requires cryptographic integrity check (HMAC or signature). |
| Disk exfiltration | **High** | A snapshot file sitting on disk is readable by any process with filesystem access. Should be encrypted at rest. |
| Version-skew silent corruption | **High** | Loading a snapshot with a different model version may produce plausible-but-wrong output. Requires strict version binding. |
| Metadata leakage | **Medium** | Snapshot metadata (model, tokenizer, timestamp, token count) reveals inference patterns. Should be minimal. |

### Privacy Requirements (if implemented)

1. **Encryption at rest**: Snapshots must be AES-256-GCM encrypted with a
   process-local key derived from a hardware-bound secret (Secure Enclave on
   macOS).
2. **Cryptographic binding**: Every snapshot must include a HMAC-SHA256 over
   the ciphertext, keyed with the same secret.
3. **User isolation**: Snapshots must be stored in per-user directories with
   OS-level file permissions (`0700`).
4. **No raw prompt storage**: Snapshot files must contain zero reconstructable
   prompt text. KV tensors are acceptable; token ID lists are not (they can
   be detokenized).
5. **Automatic expiry**: Snapshots unused for N days (default 7) must be
   automatically deleted.
6. **Opt-in only**: Users must explicitly enable durable snapshots via a
   documented configuration flag. Default must be off.

---

## Invalidation Model

If durable snapshots are implemented, the following conditions MUST invalidate
all cached snapshots for a given model:

| Condition | Detection Mechanism | Action |
|---|---|---|
| Model file changed (hash mismatch) | SHA-256 of model weights file stored in snapshot metadata | Evict all snapshots for that model |
| Tokenizer changed | SHA-256 of tokenizer config stored in metadata | Evict all snapshots for that model |
| Chat template changed | SHA-256 of chat template stored in metadata | Evict all snapshots for that model |
| `mlx-lm` / `llama.cpp` version changed | Version string stored in metadata | Evict all snapshots unconditionally |
| Quantization changed | Quantization label stored in metadata | Evict all snapshots for that model+quantization |
| Context window changed | `--ctx-size` stored in metadata | Evict all snapshots |
| User-initiated flush | Explicit API call | Evict matching snapshots |
| Snapshot age > TTL | Timestamp check | Evict expired snapshots |

### Invalidation Example

```
Snapshot metadata:
  model_hash: abc123...
  tokenizer_hash: def456...
  template_hash: 789abc...
  mlx_lm_version: 0.20.0
  quantization: 4bit
  ctx_size: 4096
  created_at: 2026-06-15T00:00:00Z

On next startup:
  - compute current model_hash → mismatch → evict
  - OR any other field changed → evict
```

---

## Disk / Memory Implications

### Estimated Snapshot Size

For a single KV cache entry (one prompt prefix):

| Model Size | Layers | Head Dim | KV (fp16) per Token | @4096 ctx | @8192 ctx |
|---|---|---|---|---|---|
| 3B (Llama 3.2) | 28 | 128 | ~14 KB | ~57 MB | ~114 MB |
| 7B (Mistral) | 32 | 128 | ~16 KB | ~64 MB | ~128 MB |
| 8B (Llama 3.1) | 32 | 128 | ~16 KB | ~64 MB | ~128 MB |

Per-token estimate: `2 × layers × head_dim × n_heads × dtype_bytes`
where `n_kv_heads` may differ from `n_heads` for GQA models.

**Realistic scenario**: With 16 active cache entries for a 3B model at 4096
context, total disk usage = 16 × 57 MB ≈ **912 MB**.  With a 7B model at 8192
context: 16 × 128 MB ≈ **2 GB**.

### I/O Impact

- **Write**: Saving a 57 MB snapshot takes ~100-200ms on Apple Silicon SSD.
  This occurs after every eligible prompt prefill.
- **Read**: Loading a 57 MB snapshot takes ~50-100ms.  Must complete before
  token generation begins — adds to time-to-first-token.
- **Disk wear**: With 100 requests/day and 50% cache hit rate, approximately
  50 writes/day × 57 MB = 2.85 GB/day written.  Within SSD endurance limits
  for modern drives but non-trivial for older hardware.

---

## Failure Modes

| Mode | Probability | Impact | Detection |
|---|---|---|---|
| Snapshot file corrupted (bit rot, partial write) | Low | Load failure or garbage output | HMAC verification on load |
| Model updated between write and read | Medium (manual updates) | Garbage output | Version hash check on load |
| Snapshot from different prompt loaded accidentally | Low (if key-binding is correct) | Wrong prefix, semantic mismatch | Cache key binding in metadata |
| Disk full during snapshot write | Low | Partial file, load failure | Atomic write (write to temp, rename) |
| Process killed during write | Low | Partial file | Atomic write; HMAC catches partial |
| Encryption key lost (Secure Enclave reset) | Low | All snapshots unreadable | Graceful deletion of orphaned files |
| Snapshot load slower than recomputation | Medium (small models, fast prefill) | Negative performance | Benchmark gate — only load if faster |
| Memory pressure during load | Medium | OOM or swap thrashing | Check available memory before load |

### Critical Failure: Silent Semantic Corruption

The most dangerous failure mode is **silent corruption**: a snapshot loads
successfully but produces subtly wrong completions.  This can happen when:
- A model is fine-tuned or quantized differently but has the same file hash
- Internal MLX Metal buffer layout changes between versions
- A snapshot from a different-but-similar model architecture is loaded

**This failure mode is undetectable at load time and only manifests as
degraded output quality.** Mitigation requires strict cryptographic binding
of all model identity dimensions, but zero risk cannot be guaranteed without
bit-exact equivalence verification (which is computationally prohibitive).

---

## Proposed MVP (if approved)

### Scope

1. **Backend**: MLX in-process only (simplest to prototype)
2. **Format**: `.npy` per tensor in a versioned directory structure
3. **Storage**: `~/.whooshd/kv-snapshots/{model_hash}/{cache_key}/`
4. **Encryption**: File-level AES-256-GCM via `cryptography` library
5. **Metadata**: JSON sidecar file with model/tokenizer/template hashes, version, timestamp
6. **Invalidation**: On startup, scan all snapshots for hash mismatches; evict invalid ones
7. **TTL**: Configurable, default 7 days
8. **Feature flag**: `WHOOSHD_THREADWAKE_DURABLE_SNAPSHOTS_ENABLED=false`
9. **API**: `ThreadWakeManager.save_snapshot(cache_key)` / `load_snapshot(cache_key)` with `EphemeralResult`-style return

### Non-scope (explicitly deferred)

- Cross-backend portability
- Cross-version portability
- Cross-machine portability
- Snapshot compression
- Incremental/delta snapshots
- Cloud sync or backup
- Multi-user isolation (single-user local-first model)

### Effort Estimate

| Component | Effort |
|---|---|
| MLX cache walker + save/load | 3–5 days |
| Metadata schema + validation | 1–2 days |
| Encryption layer | 1–2 days |
| Invalidation + TTL | 1 day |
| Manager integration | 2–3 days |
| Tests (unit + integration) | 3–5 days |
| Documentation | 1 day |
| **Total** | **12–19 days** |

---

## Reasons to Defer

1. **No backend exposes a stable, supported KV serialization API.**  Every
   backend would require reverse-engineering internal cache structures that
   are subject to change without notice.

2. **Silent corruption risk is existential for an inference product.**
   A snapshot that loads but produces subtly wrong output is worse than a
   crash — it erodes user trust without visible symptoms.

3. **The primary use case (restart persistence) has limited value for
   single-user local-first inference.** Whoosh'd typically runs as a
   long-lived sidecar process. Restarts are infrequent.  The dominant
   optimization — same-session prefix reuse — is already handled by
   ephemeral/session modes.

4. **Disk I/O may negate the latency benefit.** For small-to-medium models
   on Apple Silicon, prefill is fast enough that loading a 50–100 MB
   snapshot from disk may be slower than recomputing the KV cache from
   scratch.

5. **Encryption and key management add operational complexity.**
   The Secure Enclave integration, key rotation, and recovery paths are
   non-trivial and poorly tested in the Python ML ecosystem.

6. **The ThreadWake metadata index already provides restart-safe
   observability.** On restart, the index is empty but can be repopulated
   from normal observe-mode observations. The optimization surface lost is
   minimal.

---

## Next Steps

### Immediate (this cycle)

- [x] Research document complete
- [ ] Review with team; confirm deferral decision

### Future (re-evaluate when)

- [ ] `mlx-lm` releases a stable KV cache save/restore API (unlikely near-term)
- [ ] `llama.cpp` documents and stabilizes slot save/restore as a public contract
- [ ] A user demonstrates a concrete workflow where restart persistence provides
      measurable benefit (benchmark data required)
- [ ] Apple ships MLX with built-in cache serialization (Metal shader caching
      is a related but separate feature)

### Alternative Approaches (lower risk)

Rather than durable KV snapshots, consider:

1. **Warm-recompute on startup**: Pre-populate the ThreadWake index with
   known valuable prefixes (from a config file or heuristics) and let
   the first request of each prefix trigger a normal prefill.  No disk
   persistence of KV state required.

2. **External prompt-store integration**: If Codexify maintains a
   prompt template store, Whoosh'd could receive explicit "warm this
   prefix" hints on startup without persisting KV to disk.

3. **Session export/import (future)**: Allow exporting session metadata
   (segment hashes, thread tip, chain hashes) as a portable JSON blob
   for import on another machine — without exporting KV tensors.

---

*End of research document.*

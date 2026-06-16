# Backend Tokenizer Adapter Support Matrix

**Phase M2 Discovery Spike** — maps every Whoosh'd runtime path against
the requirements for a `BackendTokenizerAdapter`.

## Support Matrix

| Backend / Path | Prompt Rendering Owner | Tokenizer Owner | Exact Token IDs? | Segment Spans? | `tokenizer_hash`? | `chat_template_hash`? | Safe to Register? | Classification | Reason |
|---|---|---|---|---|---|---|---|---|---|
| **MLX in-process** (`mlx.py`) | `self._tokenizer.apply_chat_template()` | `mlx_lm.load()` → `self._tokenizer` | ✅ Yes — same tokenizer used for inference | ⚠️ Possible — requires segment-level tokenization | ✅ Yes — tokenizer name + vocab hash | ✅ Yes — template string hash | ⚠️ Not yet — needs span mapping proof | **possible_with_refactor** | Has real tokenizer + template; needs segment-span mapping and fidelity test |
| **llama.cpp / GGUF** (`llama_cpp.py`) | Server-side (opaque) | Server-side (opaque) | ❌ No — no local tokenizer | ❌ No — HTTP-only | ❌ No | ❌ No — server may use different template | ❌ No | **blocked** | No local tokenizer; template rendering is server-side and opaque |
| **MLX-LM Server** (`mlx_lm_server.py`) | Subprocess `mlx_lm.server` (opaque) | Subprocess (opaque) | ❌ No — no local tokenizer | ❌ No — HTTP-only | ❌ No | ❌ No | ❌ No | **blocked** | Subprocess renders prompt; Whoosh'd has no access to tokenizer or template |
| **MLX-VLM** (`mlx_vlm.py`) | Server-side (opaque) | Server-side (opaque) | ❌ No | ❌ No — multimodal complexity | ❌ No | ❌ No | ❌ No | **blocked** | No local tokenizer; multimodal rendering adds image token interleaving |
| **Stub** (`stub.py`) | Deterministic transcript | None | ❌ No | ❌ No | ❌ No | N/A | N/A | **not_applicable** | Test-only adapter; no real inference |
| **Forwarding / external routes** | Opaque external server | Opaque external server | ❌ No | ❌ No | ❌ No | ❌ No | ❌ No | **not_applicable** | Tokenization is external and untrusted |
| **FakeKVBackend** (test) | Synthetic | FakeTokenizerAdapter | ✅ Yes (hash-derived) | ✅ Yes | ❌ No | ❌ No | ✅ Tests only | **ready** (test-only) | For ThreadWake manager flow testing |

## Classification Legend

| Classification | Meaning |
|---|---|
| **ready** | Adapter can be registered now with `token_ids` or `token_ids_with_spans` |
| **possible_with_refactor** | Backend has the necessary primitives; needs integration work |
| **blocked** | Backend lacks tokenizer access or template fidelity; revisit later |
| **not_applicable** | Test/stub/forwarding path; no real tokenizer needed |

## Adapter Registration Policy

A backend may register a `BackendTokenizerAdapter` with capability
`token_ids` or `token_ids_with_spans` **only when all** of the following
conditions are met:

1. **Same rendered prompt as inference path.** The adapter must produce
   token IDs from the exact same prompt string the backend will prefill.
   If the adapter renders the prompt with one template and the backend
   uses a different template, cache reuse is incorrect.

2. **Same tokenizer as inference path.** Token IDs must come from the
   tokenizer that will consume them during prefill.  Different
   tokenizers produce different token ID sequences for the same text.

3. **Deterministic chat template hash.** The chat template used for
   rendering must be hashed and included in the `TokenizedPrompt` and
   cache key.  This ensures cache invalidation when the template changes.

4. **Deterministic tokenizer hash.** The tokenizer identity must be
   hashed (e.g., tokenizer name + vocabulary hash) and included in the
   `TokenizedPrompt`.  This ensures cache invalidation when the
   tokenizer changes.

5. **Exact segment span mapping OR conservative all-prefix mode.**
   If the adapter cannot map token spans to individual segments,
   it must at minimum correctly split stable prefix token IDs from
   dynamic tail token IDs.  Conservative mode: report all prompt
   tokens as the stable prefix (no segmentation).

6. **Test proving tokenization path matches inference prompt path.**
   A test must demonstrate that the token IDs produced by the adapter
   match the token IDs the backend would produce from the same rendered
   prompt.

## Current State

| Adapter | Capability | Registered? |
|---|---|---|
| `NoOpTokenizerAdapter` | `unsupported` | Default for unregistered backends |
| `FakeTokenizerAdapter` | `token_ids_with_spans` | Tests/benchmarks only |
| `MLXTokenizerAdapterStub` | `estimates_only` | Not registered |
| `LlamaCppTokenizerAdapterStub` | `unsupported` | Not registered |
| `MlxLmServerTokenizerAdapterStub` | `unsupported` | Not registered |
| `MlxVlmTokenizerAdapterStub` | `unsupported` | Not registered |
| `ForwardingTokenizerAdapterStub` | `unsupported` | Not registered |

**No production backend reports `token_ids` or `token_ids_with_spans`.
Production KV reuse remains disabled.**

## Recommended Phase M3 Target

**MLX in-process (`mlx.py`)** is the only backend with a clear path to
real tokenization:

- Has direct access to the tokenizer via `mlx_lm.load()`
- Renders the exact prompt via `apply_chat_template()`
- Uses the same tokenizer for inference via `mlx_lm.generate()`
- Is the primary local inference path on Apple Silicon

Phase M3 should:
1. Implement `MLXTokenizerAdapter` that wraps the MLX adapter's tokenizer
2. Tokenize the rendered prompt and compute segment spans
3. Produce real `token_ids` and `stable_prefix_token_ids`/`dynamic_tail_token_ids`
4. Add a fidelity test proving the tokenization path matches the inference path
5. Gate behind a feature flag — do not enable KV reuse until proven safe

## Known Blockers for Other Backends

| Backend | Blocker |
|---|---|
| llama.cpp | No local tokenizer; would need to call server tokenize endpoint or bundle a tokenizer |
| MLX-LM Server | No local tokenizer; subprocess is opaque; would need server-side endpoint |
| MLX-VLM | Multimodal tokenization is complex (image token interleaving); blocked until text path proven |
| Forwarding | External server tokenization is untrusted by definition |

---

*End of discovery matrix.  Phase M2 complete.*

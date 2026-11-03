# MLX Batch Execution — Evaluation Note

**Roadmap phase P3.** Evaluate existing experimental work; decide whether to
implement, extend, or defer. No implementation follows without this note.

**Status: EVALUATED — DEFER.** No change to the shipped batching path.

---

## What already exists

Batch execution is **not** a greenfield design. The following work is in the
tree today:

| Existing work | Where | State |
|---|---|---|
| Guarded adapter batching | `whooshd/guarded_adapter_batching.py` | Built, smoke-validated, HTTP-grouping validated, documented, **explicitly gated and disabled by default** |
| MLX `batch_generate` capability probe | `whooshd/adapters/mlx_batch.py` — `probe_mlx_batch_generate_capability()` | Present; reports `FEASIBLE` / `INCONCLUSIVE` by signature inspection |
| `prompt_cache` support detection | `whooshd/adapters/mlx_batch.py` — `_check_batch_prompt_cache()` | Present; inspects the `batch_generate` signature |
| Batch capability signal on adapters | `whooshd/adapters/mlx.py:79`, `whooshd/adapters/stub.py:67` — `supports_chat_batching` | Present and exposed |
| Live `batch_generate` call | `whooshd/adapters/mlx.py:125` | Present in the in-process MLX adapter's chat-batch path |
| Batch queue grouping | `whooshd/queue.py` — `build_batch_candidates()`, `find_batch_group()`, `claim_batch_entries()` | Present |
| Batch capacity interaction | `tests/test_batch_capacity_interaction.py` | Covered |

**Caveat on the existing work:** the live `batch_generate` call at
`whooshd/adapters/mlx.py:125` sits on the **in-process MLX adapter**, which
`README.md:45` marks *"Supported, legacy"*. The preferred MLX text path is the
**MLX-LM Server** lane (`README.md:44`), and that lane does not use
`batch_generate` — it forwards to an upstream `mlx_lm.server`. So the existing
native-batch call site is on a lane that is not the preferred one, and the
preferred lane has no native batch path at all.

That distinction matters for any decision below.

---

## The three options, on measured evidence

### 1. Guarded adapter batching as shipped — **retain**

The shipped path. Gated by environment variable, off by default, validated
within scoped test conditions.

**Measured evidence:** `docs/release-notes/guarded-adapter-batching.md`,
`docs/validation-index.md`. The documented scope is explicit and the claim
boundary is preserved: *not production-ready, not token-step continuous
batching, no latency or throughput improvement claimed.*

**Assessment:** keep as-is. It is the only batching path with a validation
packet behind it.

### 2. MLX-native `batch_generate` — **do not extend yet**

**Arguments for:** the API exists, the capability probe exists, and the
`supports_chat_batching` signal is already exposed. Native batching would
avoid a Whoosh'd-side batching layer entirely.

**Arguments against, and they are decisive here:**

- The only live call site is on the **legacy in-process lane**. Extending it
  means investing in a lane the project does not prefer.
- The **preferred lane** (MLX-LM Server) forwards to an upstream server that
  does its own scheduling. Adding a batching layer in front of it would
  duplicate work the upstream already does, and would mean two schedulers
  deciding the same thing.
- `batch_generate` in MLX requires **compatible generation settings across
  the batch** (sampling parameters, and a shared prompt shape). That
  constrains which requests can be grouped — a real limitation for a
  multi-tenant chat deployment where tenants differ in `temperature`,
  `max_tokens`, and context size.
- No benchmark packet exists comparing native batching against the guarded
  path on this host.

**Assessment:** defer. Revisit only if (a) the preferred lane gains a
native-batch path, or (b) a benchmark packet shows a measurable advantage for
native batching over the guarded path on this host, or (c) a deployment needs
batch grouping that the guarded path's constraints cannot express.

### 3. Deferral — **adopt as the decision**

Adopt option 3 as the outcome of this evaluation.

---

## Decision

**Defer. Retain the guarded adapter batching path as the only batching path.**

Nothing about the shipped behaviour changes. No code follows this note.

---

## What would reopen this

A concrete, evidence-bearing trigger — not a general appetite for batching:

1. A **benchmark packet** on the target host comparing, at equal concurrency
   bands: the guarded path, MLX-native `batch_generate`, and the no-batching
   baseline — with TTFT, aggregate and per-request token throughput, and queue
   latency recorded per band.
2. Evidence that the **preferred lane** can batch natively, so the
   comparison is between two real options rather than between a preferred
   lane and a legacy one.
3. A deployment that demonstrably needs request grouping the guarded path's
   compatibility constraints cannot express.

Absent one of these, adding a second batching mechanism increases the
control plane's surface without evidence that it improves anything.

---

## Boundaries that survive this decision

- **Cave Thunder stands.** True token-step shared decode scheduling remains
  research-only for MLX under the current integration
  (`docs/token-step-cave-thunder-decision.md`). This evaluation does not
  reopen it.
- **No throughput claim is made** by this note. The guarded path continues to
  claim nothing about latency or throughput beyond its recorded packet.
- **Batching is not on by default** and remains explicitly gated.
- Whoosh'd remains a **control plane**: batching is a scheduling decision the
  broker may make, not an inference engine the broker becomes.

# ThreadWake + Codexify Integration

Codexify can optionally provide ThreadWake with explicit segment metadata
to improve prefix identification accuracy.  This page documents the
integration contract from both sides.

---

## Overview

Whoosh'd infers prompt segment boundaries from OpenAI message roles
(system → stable, user → dynamic, etc.).  Codexify, as the prompt
assembler, knows much more about the prompt structure:

- Guardian system prompt
- Persona layer
- Tool manifest / instructions
- Project context
- Retrieval / RAG bundle
- Prior conversation history
- Latest user message
- Tool outputs

By sending this metadata alongside the chat completion request, Codexify
enables Whoosh'd to make more accurate cacheability decisions.

---

## Request Contract

### `threadwake` Block

Added to the chat completion request body when `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED=true`
and `LOCAL_PROVIDER_VENDOR=whooshd`:

```json
{
  "threadwake": {
    "enabled": true,
    "mode": "observe",
    "scope": "thread"
  }
}
```

### `threadwake_segments` Array

Each entry describes one message in the `messages` array:

```json
{
  "threadwake_segments": [
    {
      "name": "guardian_system",
      "message_index": 0,
      "segment_type": "system",
      "stability": "stable",
      "scope": "user",
      "content_hash": "sha256..."
    },
    {
      "name": "persona_layer",
      "message_index": 1,
      "segment_type": "persona",
      "stability": "stable",
      "scope": "user"
    },
    {
      "name": "tool_manifest",
      "message_index": 2,
      "segment_type": "tools",
      "stability": "stable",
      "scope": "project"
    },
    {
      "name": "project_context",
      "message_index": 3,
      "segment_type": "project",
      "stability": "semi_stable",
      "scope": "project"
    },
    {
      "name": "retrieval_bundle",
      "message_index": 4,
      "segment_type": "retrieval",
      "stability": "semi_stable",
      "scope": "thread"
    },
    {
      "name": "conversation_history",
      "message_index": 5,
      "segment_type": "thread",
      "stability": "semi_stable",
      "scope": "thread"
    },
    {
      "name": "latest_user_message",
      "message_index": 6,
      "segment_type": "user",
      "stability": "dynamic",
      "scope": "request"
    }
  ]
}
```

### Field Reference

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Human-readable segment identifier |
| `message_index` | Yes | Index into the `messages` array |
| `segment_type` | No | One of: `system`, `persona`, `tools`, `project`, `retrieval`, `thread`, `user`, `tool_output`, `unknown` |
| `stability` | No | One of: `stable`, `semi_stable`, `dynamic`. Omit to use type defaults. |
| `scope` | No | One of: `request`, `thread`, `project`, `user`, `global`. Default: `thread`. |
| `content_hash` | No | SHA-256 of the message content. If provided and mismatched, the metadata entry is rejected. |
| `cacheable` | No | When `false`, forces the segment to `dynamic` (excluded from stable prefix). |

### Validation Rules

Whoosh'd validates all metadata against actual message content:

1. `message_index` must be a valid index into the `messages` array.
2. If `content_hash` is provided, it must match SHA-256 of the canonicalized
   message content.  Mismatched hashes cause the metadata entry to be
   ignored (falls back to inferred segmentation).
3. `scope: "global"` is rejected unless `WHOOSHD_THREADWAKE_ALLOW_GLOBAL=true`.
4. `segment_type: "tool_output"` and `"user"` are always forced to
   `dynamic` stability regardless of the requested stability.
5. `segment_type: "retrieval"` with `stability: "stable"` but no
   `content_hash` is degraded to `semi_stable`.
6. Duplicate `message_index` values: first entry wins, subsequent
   duplicates are ignored.
7. Invalid entries degrade to inferred behavior — they never block
   inference.

---

## Segment Type Mapping

| Codexify Segment | Whoosh'd Internal Type | Default Stability | Default Scope |
|---|---|---|---|
| `system` | `system` | `stable` | `user` |
| `persona` | `persona` | `stable` | `user` |
| `tools` | `tool_schema` | `stable` | `project` |
| `project` | `project_context` | `semi_stable` | `project` |
| `retrieval` | `retrieval` | `semi_stable` | `thread` |
| `thread` | `thread_history` | `semi_stable` | `thread` |
| `user` | `user_message` | `dynamic` | `request` |
| `tool_output` | `tool_output` | `dynamic` | `request` |
| `unknown` | `unknown` | `dynamic` | `thread` |

---

## Provider Panel Integration

Codexify's local provider panel can display ThreadWake status via the
`GET /health/threadwake` endpoint.  The panel shows:

- **Status**: OFF / OBSERVING / READY / DEGRADED / Unavailable
- **Mode**: observe / ephemeral / session
- **Cache Hit Rate**: computed from `total_hits / (total_hits + total_misses)`
- **Ready Entries**: e.g., "3 / 16"
- **Est. Memory**: formatted bytes (MB / GB)
- **Disclaimer**: "ThreadWake reuses compatible computed prompt-prefix state
  for supported local models. It is a runtime optimization, not long-term
  memory."

The panel degrades gracefully when the endpoint is unreachable (shows
"Unavailable" or hides the section entirely).

---

## Configuration (Codexify Side)

| Environment Variable | Default | Description |
|---|---|---|
| `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED` | `false` | Emit segment metadata |
| `CODEXIFY_WHOOSHD_THREADWAKE_MODE` | `observe` | ThreadWake mode |
| `CODEXIFY_WHOOSHD_THREADWAKE_SCOPE` | `thread` | Default cache scope |

Metadata emission only activates when **all** conditions are met:
1. `CODEXIFY_WHOOSHD_THREADWAKE_SEGMENTS_ENABLED=true`
2. `LOCAL_PROVIDER_VENDOR=whooshd` (or equivalent preset)
3. The request uses the local provider path

---

## Safe Wording Guidelines

When describing ThreadWake in Codexify's UI or documentation:

| ✅ Do | ❌ Don't |
|---|---|
| "runtime optimization" | "AI memory" |
| "prompt-prefix reuse" | "remembers conversations" |
| "cache hit rate" | "memory recall rate" |
| "compatible precomputed state" | "learned from you" |
| "ThreadWake Cache" | "Memory" or "Recall" |
| "not long-term memory" | "persistent memory" |

The panel disclaimer is:
> ThreadWake reuses compatible computed prompt-prefix state for supported
> local models. It is a runtime optimization, not long-term memory.

---

## Current Limitations

- **Coarse segmentation in Codexify**: The current segment metadata
  emission uses message roles (system → system, user → user, etc.).
  Finer segmentation (distinguishing guardian vs persona within system
  messages) requires the prompt builder to expose its internal layer
  boundaries.
- **No thread/user ID hashing on the Codexify side**: The `threadwake`
  config block currently omits `metadata.user_id_hash` and
  `metadata.thread_id` because the local provider call path doesn't have
  access to session-level identifiers.
- **Metadata is advisory only**: Invalid metadata degrades to inferred
  segmentation — it never blocks or errors the request.

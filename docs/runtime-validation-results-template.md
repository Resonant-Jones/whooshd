# Runtime Validation Results Template

Use this template to record real local runtime validation results.
Copy it and fill in your values.

---

## Machine Info

| Field | Value |
|-------|-------|
| Machine | (e.g. MacBook Pro M3 Max, 64 GB) |
| OS | (e.g. macOS 15.0) |
| Python | (e.g. 3.14.3) |
| Whoosh'd commit | (e.g. abc1234) |
| llama.cpp version | (e.g. b4937) |
| mlx-lm version | (e.g. 0.24.0) |

## Models Tested

| Model | Format | Size | Source |
|-------|--------|------|--------|
| (e.g. Llama-3.2-3B-Instruct-4bit) | MLX | ~2 GB | mlx-community |
| (e.g. qwen3-coder-30b-q4_k_m) | GGUF | ~18 GB | local |

## Runtime Mode

How was each runtime started?

```bash
# llama.cpp
# (paste exact command here)

# MLX-LM Server
# (paste exact command here)

# Whoosh'd
# (paste exact command here)
```

## Check Matrix

### llama.cpp (real)

| Check | Status | Detail |
|-------|--------|--------|
| Dependency: llama-server | pass / blocked | (path or error) |
| GET /health | | |
| GET /health/runtime | | |
| GET /ready | | |
| GET /v1/models | | |
| GET /api/tags | | |
| POST /v1/chat/completions (non-streaming) | | |
| POST /v1/chat/completions (streaming) | | |
| Codexify SSE compat | | |
| Concurrent streaming (x2) | | |

**Result: PASS / FAIL / BLOCKED**

### MLX-LM Server (real)

| Check | Status | Detail |
|-------|--------|--------|
| Dependency: mlx-lm | pass / blocked | (version or error) |
| GET /health | | |
| GET /health/runtime | | |
| GET /ready | | |
| GET /v1/models | | |
| GET /api/tags | | |
| POST /v1/chat/completions (non-streaming) | | |
| POST /v1/chat/completions (streaming) | | |
| Codexify SSE compat | | |
| Concurrent streaming (x2) | | |

**Result: PASS / FAIL / BLOCKED**

## Streaming Details

### llama.cpp
```
TTFT (first request): ___ ms
Total time (first request): ___ ms
TTFT (concurrent avg): ___ ms
Chunks received: ___
Visible text: ___
[DONE] observed: yes / no
```

### MLX-LM Server
```
TTFT (first request): ___ ms
Total time (first request): ___ ms
TTFT (concurrent avg): ___ ms
Chunks received: ___
Visible text: ___
[DONE] observed: yes / no
```

## Non-Streaming Details

### llama.cpp
```
Status: ___
Content length: ___ chars
Finish reason: ___
Response time: ___ ms
```

### MLX-LM Server
```
Status: ___
Content length: ___ chars
Finish reason: ___
Response time: ___ ms
```

## Model Inventory

### /v1/models
```
Model count: ___
Models: (list)
```

### /api/tags
```
Tag count: ___
Tags: (list)
```

## Per-Runtime Health

```json
(paste output of GET /health/runtime)
```

## Notes

(Any observations, warnings, unexpected behavior)

## Known Issues

(Anything that failed or behaved unexpectedly)

## Follow-Up

(Recommended next steps)

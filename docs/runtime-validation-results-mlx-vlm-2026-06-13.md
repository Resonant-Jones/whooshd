# MLX-VLM Runtime Validation Results — 2026-06-13

Real local runtime validation of Whoosh'd against `mlx-vlm` server.

## Machine Info

| Field | Value |
|-------|-------|
| Machine | Apple Silicon (Mac) |
| OS | macOS (darwin) |
| Python | 3.14.3 |
| Whoosh'd version | 0.1.0rc1 |
| mlx-vlm version | 0.5.0 |
| MLX-VLM model | `mlx-community/Qwen2-VL-2B-Instruct-4bit` |
| Model format | MLX (4-bit quantized) |
| Model size | ~2 GB |
| Image fixture | `tests/fixtures/vision/color_shapes.png` (1.5 KB, 256×256) |
| Runtime mode | External server |
| Date | 2026-06-13 |

## Phase 10 Hardening Results

### Semantic Vision Smoke

```bash
python scripts/validate_local_runtimes.py \
  --runtime mlx-vlm \
  --whooshd-url http://127.0.0.1:8000 \
  --model "mlx-community/Qwen2-VL-2B-Instruct-4bit" \
  --image tests/fixtures/vision/color_shapes.png \
  --vision-prompt "What shapes and colors are visible in this image? Answer concisely." \
  --expect-text red --expect-text square --expect-text blue --expect-text circle \
  --concurrency 1
```

**Model answer:** "The image contains a red square and a blue circle."

| Term | Matched? |
|------|----------|
| red | ✅ |
| square | ✅ |
| blue | ✅ |
| circle | ✅ |

**Result: 10/10 PASS** (all checks including semantic match)

### Concurrency x2

```bash
python scripts/validate_local_runtimes.py \
  --runtime mlx-vlm \
  --whooshd-url http://127.0.0.1:8000 \
  --model "mlx-community/Qwen2-VL-2B-Instruct-4bit" \
  --image tests/fixtures/vision/color_shapes.png \
  --vision-prompt "What shapes and colors are visible in this image?" \
  --concurrency 2
```

```
Concurrent x2: ok=2/2 avg_ttft=120ms stuck=0 overloaded=0 empty=0
```

**Result: 10/10 PASS.** Both requests completed normally.

### Streaming Details (256×256 fixture)

```
Chunks: 12-15 (varies by run)
[DONE]: yes
Content-type: text/event-stream
TTFT (x2 avg): 120ms
Total latency (non-streaming): ~550-680ms
```

### Non-Streaming Details

```
Status: 200
Content: "The image contains a red square and a blue circle."
Finish reason: stop
Response time: ~550-680ms
```

## Check Matrix

| Check | x1 (semantic) | x2 (concurrency) |
|-------|--------------|-------------------|
| Dependency: mlx-vlm | ✅ PASS | ✅ PASS |
| /health | ✅ PASS | ✅ PASS |
| /health/runtime | ✅ PASS | ✅ PASS |
| /ready | ✅ PASS | ✅ PASS |
| /v1/models | ✅ PASS | ✅ PASS |
| /api/tags | ✅ PASS | ✅ PASS |
| Non-streaming | ✅ PASS | ✅ PASS |
| Streaming | ✅ PASS | ✅ PASS |
| Codexify SSE compat | ✅ PASS | ✅ PASS |
| Vision semantic check | ✅ PASS (4/4 terms) | — |
| Concurrent x2 | — | ✅ PASS (2/2, 0 stuck) |

## Known Issues

1. **Single vision model tested** — Only Qwen2-VL-2B.
2. **Red shape initially rectangle** — First fixture version had 128×192 red shape;
   model correctly identified it as "rectangle". Updated to 96×96 perfect square.
3. **mlx-vlm response latency** — ~500-680ms for non-streaming, ~500ms for streaming.
   Higher than text-only mlx_lm.server (~150-200ms). Expected for vision models.

## llama.cpp Status

```
blocked: llama-server binary unavailable
```

## Follow-Up

1. Install llama-server + GGUF model for llama.cpp validation
2. Test additional vision models (Qwen2-VL-7B, Pixtral, etc.)
3. Test with photographic images (not just geometric shapes)
4. Test Codexify end-to-end if available

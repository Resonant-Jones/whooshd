# MLX-VLM Runtime Validation Results Template

Use this template to record real MLX-VLM vision runtime validation.

## Prerequisites

* `mlx-vlm` installed (`pip install mlx-vlm`)
* A vision-language MLX model
* Whoosh'd running

## Machine Info

| Field | Value |
|-------|-------|
| Machine | |
| OS | |
| Python | |
| Whoosh'd version | |
| mlx-vlm version | |
| MLX-VLM model | |
| Model format | MLX |
| Runtime mode | External server |
| Date | |

## Startup Commands

### MLX-VLM Server

```bash
.venv/bin/python -m mlx_vlm server \
  --model "<your-vision-model>" \
  --host 127.0.0.1 \
  --port 8082
```

### Whoosh'd

```bash
WHOOSHD_MLX_VLM_ENABLED=true \
  WHOOSHD_MLX_VLM_MODEL="<your-vision-model>" \
  WHOOSHD_MLX_VLM_HOST=127.0.0.1 \
  WHOOSHD_MLX_VLM_PORT=8082 \
  WHOOSHD_MLX_VLM_MAX_CONCURRENT_REQUESTS=1 \
  python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
```

### Validation Harness

```bash
python scripts/validate_local_runtimes.py \
  --runtime mlx-vlm \
  --whooshd-url http://127.0.0.1:8000 \
  --model "<your-vision-model>" \
  --concurrency 2
```

## Image Request Shape

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<your-vision-model>",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Describe this image in one sentence."},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]
      }
    ],
    "stream": false
  }' | jq .
```

## Check Matrix

### MLX-VLM (real)

| Check | Status | Detail |
|-------|--------|--------|
| Dependency: mlx-vlm | | |
| GET /health | | |
| GET /health/runtime | | |
| GET /ready | | |
| GET /v1/models | | |
| GET /api/tags | | |
| POST /v1/chat/completions (non-streaming, text) | | |
| POST /v1/chat/completions (non-streaming, image) | | |
| POST /v1/chat/completions (streaming, text) | | |
| Codexify SSE compat | | |
| Concurrent streaming (x2) | | |
| Image request to text model → rejected | | |

**Result: PASS / FAIL / BLOCKED**

## Notes

## Known Issues

## Follow-Up

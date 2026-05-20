# Whoosh'd Release Checklist

Checklist for validating a Whoosh'd release candidate.

---

## Automated

- [ ] `python -m pytest -v` passes (all tests)
- [ ] Default install works without `mlx-lm` (`pip install -e .`)
- [ ] Optional `mlx` extra installs (`pip install -e ".[mlx]"`)
- [ ] Stub smoke probe passes
- [ ] Benchmark CLI help works (`python -m whooshd.bench.runner --help`)

---

## Manual Stub

- [ ] Start stub server (`WHOOSHD_ADAPTER=stub python -m uvicorn whooshd.app:app`)
- [ ] `GET /health` returns 200
- [ ] `GET /ready` returns 200
- [ ] `GET /v1/models` returns usable inventory
- [ ] `GET /api/tags` returns usable inventory
- [ ] `POST /v1/chat/completions` non-streaming works
- [ ] `POST /v1/chat/completions` streaming works (SSE → `[DONE]`)
- [ ] Benchmark `stub-stream-smoke` passes
- [ ] Benchmark `stub-overload` shows 429 rejections

---

## Manual MLX

- [ ] Install optional `mlx` extra
- [ ] Start MLX server (`WHOOSHD_ADAPTER=mlx`)
- [ ] `POST /runtime/model/warmup` succeeds
- [ ] `GET /ready` returns 200
- [ ] Non-streaming smoke returns valid `chat.completion`
- [ ] Streaming smoke returns SSE chunks → `[DONE]`
- [ ] `active_jobs` returns to 0 after smoke
- [ ] Benchmark `mlx-warm-single` completes
- [ ] Benchmark `mlx-warm-concurrent-2` completes

---

## Codexify Integration

- [ ] Codexify runtime available
- [ ] `LOCAL_BASE_URL` configured
- [ ] Model discovered by Codexify
- [ ] Single chat turn streams successfully
- [ ] Concurrent chat turns tested
- [ ] Overload behavior tested (429 handling)
- [ ] `429 RUNNER_OVERLOADED` not treated as offline

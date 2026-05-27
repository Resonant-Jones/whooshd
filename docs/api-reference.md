# Whoosh'd API Reference

This is the compact contract reference for the local provider surface that
Codexify consumes.

## Inventory Endpoints

- `GET /v1/models`
- `GET /api/tags`

Both endpoints must advertise the exact configured model id.

- Stub mode advertises `stub-model`
- MLX mode advertises `WHOOSHD_MLX_MODEL` verbatim

That contract lets Codexify validate `LOCAL_CHAT_MODEL` without relaxing its
provider gate or guessing at a stale alias.

## Related Endpoints

- `GET /health`
- `GET /ready`
- `GET /runtime`
- `GET /runtime/model`
- `POST /runtime/model/warmup`
- `POST /runtime/model/unload`
- `POST /v1/chat/completions`
- `POST /v1/generate`


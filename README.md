# Whoosh'd

Memory-aware local inference broker for Apple Silicon.

## Local Dev

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run the API server
python -m uvicorn whooshd.app:app --reload

# Run tests
python -m pytest -v
```

## Endpoints (v0)

| Method | Path        | Description                            |
|--------|-------------|----------------------------------------|
| GET    | `/health`   | Liveness check + basic state           |
| GET    | `/runtime`  | Full runtime snapshot                  |
| GET    | `/models`   | Registered models and their load state |

Open http://127.0.0.1:8000/docs for the auto-generated Swagger UI.

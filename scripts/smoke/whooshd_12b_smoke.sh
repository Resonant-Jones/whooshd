#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_URL="${UPSTREAM_URL:-http://127.0.0.1:8082}"
WHOOSHD_URL="${WHOOSHD_URL:-http://127.0.0.1:8000}"
MODEL_ID="${MODEL_ID:-gemma-4-12b-it-qat-4bit}"
EXPECTED_TEXT="${EXPECTED_TEXT:-operational}"
PROMPT="${PROMPT:-Reply with exactly: operational}"

command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

upstream_models="$tmp_dir/upstream-models.json"
whooshd_models="$tmp_dir/whooshd-models.json"
chat_resp="$tmp_dir/chat.json"

curl -fsS "$UPSTREAM_URL/v1/models" > "$upstream_models"
python3 - "$upstream_models" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
models = data.get("data", [])
if not models:
    raise SystemExit("Upstream returned no models")
print(f"upstream models: {len(models)}")
PY

curl -fsS "$WHOOSHD_URL/v1/models" > "$whooshd_models"
python3 - "$whooshd_models" "$MODEL_ID" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
model_id = sys.argv[2]
ids = [entry.get("id") for entry in data.get("data", [])]
if model_id not in ids:
    raise SystemExit(f"{model_id} missing from /v1/models: {ids}")
print(f"whooshd inventory contains {model_id}")
PY

curl -fsS "$WHOOSHD_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(python3 - "$MODEL_ID" "$PROMPT" <<'PY'
import json, sys
print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": sys.argv[2]}],
    "stream": False,
    "max_tokens": 16,
}))
PY
)" > "$chat_resp"

python3 - "$chat_resp" "$EXPECTED_TEXT" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
expected = sys.argv[2]
content = data["choices"][0]["message"]["content"].strip()
if content != expected:
    raise SystemExit(f"Unexpected content: {content!r} != {expected!r}")
print(f"chat completion returned {content!r}")
PY

echo "whooshd_12b_smoke: PASS"

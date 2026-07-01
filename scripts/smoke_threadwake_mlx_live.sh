#!/bin/sh
# Whoosh'd live MLX ThreadWake smoke test — requires the server to already be running.
#
# Prerequisites:
#   - Apple Silicon (M-series)
#   - macOS 14+
#   - mlx-lm installed
#   - A local MLX model available
#
# Start the server first with experimental MLX KV enabled, for example:
#
#   WHOOSHD_ADAPTER=mlx \
#   WHOOSHD_THREADWAKE_ENABLED=true \
#   WHOOSHD_THREADWAKE_MODE=ephemeral \
#   WHOOSHD_THREADWAKE_MLX_TOKENIZER_ENABLED=true \
#   WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true \
#   WHOOSHD_MLX_MODEL="mlx-community/Llama-3.2-3B-Instruct-4bit" \
#   python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
#
# Then run:
#
#   sh scripts/smoke_threadwake_mlx_live.sh
#
# Optional:
#   WHOOSHD_BASE_URL=http://127.0.0.1:8000 sh scripts/smoke_threadwake_mlx_live.sh
#   WHOOSHD_MLX_SMOKE_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit sh scripts/smoke_threadwake_mlx_live.sh
#
# This is a manual smoke test — not a benchmark.  It proves the experimental
# MLX prompt-cache path works against a real loaded model.  It does not claim
# production acceleration.

BASE_URL="${WHOOSHD_BASE_URL:-http://127.0.0.1:8000}"
MODEL="${WHOOSHD_MLX_SMOKE_MODEL:-}"

python3 - "$BASE_URL" "$MODEL" <<'PY'
from __future__ import annotations

import http.client
import json
import sys
import uuid
from urllib.parse import urlparse

base_url = sys.argv[1].rstrip("/")
model_arg = sys.argv[2].strip()
parsed = urlparse(base_url)

host = parsed.hostname or "127.0.0.1"
port = parsed.port or (443 if parsed.scheme == "https" else 80)
scheme = parsed.scheme or "http"

if scheme != "http":
    print("FAIL  smoke currently supports http:// base URLs only")
    sys.exit(1)

secret = f"mlx-tw-smoke-{uuid.uuid4().hex[:8]}"
pass_count = 0
fail_count = 0

def pass_(name: str) -> None:
    global pass_count
    print(f"  PASS  {name}")
    pass_count += 1

def fail(name: str, detail: str = "") -> None:
    global fail_count
    if detail:
        print(f"  FAIL  {name}: {detail}")
    else:
        print(f"  FAIL  {name}")
    fail_count += 1

def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 30.0):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        data = {"_raw": raw.decode("utf-8", errors="replace")}
    return resp.status, data

def get_threadwake_health():
    status, data = request_json("GET", "/health/threadwake")
    if status != 200:
        raise RuntimeError(f"/health/threadwake returned {status}: {data}")
    return data

def get_threadwake_analysis():
    status, data = request_json("GET", "/runtime/threadwake/analysis")
    if status != 200:
        raise RuntimeError(f"/runtime/threadwake/analysis returned {status}: {data}")
    return data

def choose_model() -> str:
    if model_arg:
        return model_arg
    status, data = request_json("GET", "/v1/models")
    if status == 200:
        items = data.get("data") or data.get("models") or []
        if items and isinstance(items[0], dict) and items[0].get("id"):
            return items[0]["id"]
    fail("model selection", "no model found and WHOOSHD_MLX_SMOKE_MODEL not set")
    return ""

def post_chat(payload: dict, timeout: float = 60.0):
    return request_json("POST", "/v1/chat/completions", payload=payload, timeout=timeout)

print("Whoosh'd Live MLX ThreadWake Smoke")
print(f"  base:  {base_url}")

model = choose_model()
if not model:
    print("")
    print("Set WHOOSHD_MLX_SMOKE_MODEL to the model ID, for example:")
    print("  WHOOSHD_MLX_SMOKE_MODEL=mlx-community/Llama-3.2-3B-Instruct-4bit")
    print("")
    print("---")
    print(f"Passed: {pass_count}  Failed: {fail_count}")
    sys.exit(1)

print(f"  model: {model}")
print("")

# ── Server liveness and readiness ──────────────────────────────────────────

try:
    status, _ = request_json("GET", "/health")
    if status == 200:
        pass_("/health reachable")
    else:
        fail("/health reachable", f"status {status}")
except Exception as exc:
    fail("/health reachable", str(exc))
    print("---")
    print(f"Passed: {pass_count}  Failed: {fail_count}")
    sys.exit(1)

try:
    status, data = request_json("GET", "/ready")
    if status == 200:
        pass_("/ready reachable")
    else:
        fail("/ready reachable", f"status {status} — model may not be loaded yet")
except Exception as exc:
    fail("/ready reachable", str(exc))

# ── Model inventory ────────────────────────────────────────────────────────

try:
    status, data = request_json("GET", "/v1/models")
    if status == 200:
        model_ids = [m.get("id", "") for m in (data.get("data") or [])]
        if model in model_ids:
            pass_("/v1/models contains selected model")
        else:
            fail("/v1/models contains selected model", f"model '{model}' not in {model_ids}")
    else:
        fail("/v1/models", f"status {status}")
except Exception as exc:
    fail("/v1/models", str(exc))

# ── ThreadWake health ──────────────────────────────────────────────────────

try:
    tw_health = get_threadwake_health()
    if tw_health.get("enabled") is True:
        pass_("ThreadWake enabled")
    else:
        fail("ThreadWake enabled", f"got enabled={tw_health.get('enabled')}")

    mode = tw_health.get("mode", "")
    if mode in ("ephemeral", "session"):
        pass_(f"ThreadWake mode is {mode}")
    else:
        fail("ThreadWake mode is ephemeral", f"got mode={mode}")

    caps = tw_health.get("backend_capabilities", {})
    mlx_cap = caps.get("mlx", "unsupported")
    if mlx_cap in ("experimental", "prefill_only", "resumable"):
        pass_(f"MLX KV capability is {mlx_cap}")
    else:
        fail(
            "MLX KV capability is experimental",
            f"got {mlx_cap}. Start with WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true",
        )
except Exception as exc:
    fail("ThreadWake health check", str(exc))

if fail_count:
    print("")
    print("Start the server with:")
    print("  WHOOSHD_ADAPTER=mlx")
    print("  WHOOSHD_THREADWAKE_ENABLED=true")
    print("  WHOOSHD_THREADWAKE_MODE=ephemeral")
    print("  WHOOSHD_THREADWAKE_MLX_TOKENIZER_ENABLED=true")
    print("  WHOOSHD_THREADWAKE_MLX_KV_EXPERIMENTAL=true")
    print(f"  WHOOSHD_MLX_MODEL=\"{model}\"")
    print("")
    print("---")
    print(f"Passed: {pass_count}  Failed: {fail_count}")
    sys.exit(1)

# ── Pre-request health snapshot ────────────────────────────────────────────

tw_before = get_threadwake_health()
entries_before = tw_before.get("entry_count", 0)
hits_before = tw_before.get("total_hits", 0)
misses_before = tw_before.get("total_misses", 0)

# ── Request 1: should populate or attempt cache state ──────────────────────

system_prompt = f"[{secret}] You are a concise assistant. Keep answers short."
request1 = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Say READY in one short sentence."},
    ],
    "threadwake": {
        "enabled": True,
        "mode": "ephemeral",
        "scope": "thread",
        "min_stable_prefix_tokens": 1,
    },
    "stream": False,
    "max_tokens": 24,
}

status1, data1 = post_chat(request1)
if status1 == 200 and "choices" in data1:
    content1 = data1.get("choices", [{}])[0].get("message", {}).get("content", "")
    pass_("first request returned valid chat completion")
else:
    fail("first request returned valid chat completion", f"status={status1}")

# ── Request 2: same stable prefix, different user message ──────────────────

request2 = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Say DONE in one short sentence."},
    ],
    "threadwake": {
        "enabled": True,
        "mode": "ephemeral",
        "scope": "thread",
        "min_stable_prefix_tokens": 1,
    },
    "stream": False,
    "max_tokens": 24,
}

status2, data2 = post_chat(request2)
if status2 == 200 and "choices" in data2:
    content2 = data2.get("choices", [{}])[0].get("message", {}).get("content", "")
    pass_("second request returned valid chat completion")
else:
    fail("second request returned valid chat completion", f"status={status2}")

# Both must have content.
if content1:
    pass_("first request produced non-empty content")
else:
    fail("first request produced non-empty content", "empty response")

if content2:
    pass_("second request produced non-empty content")
else:
    fail("second request produced non-empty content", "empty response")

# ── Post-request ThreadWake activity ───────────────────────────────────────

tw_after = get_threadwake_health()
entries_after = tw_after.get("entry_count", 0)
hits_after = tw_after.get("total_hits", 0)
misses_after = tw_after.get("total_misses", 0)

if entries_after > entries_before:
    pass_("ThreadWake entry count increased after requests")
else:
    fail(
        "ThreadWake entry count increased after requests",
        f"before={entries_before} after={entries_after}",
    )

# Cache activity: should observe at least one miss (first request) and
# possibly a hit (second request with same stable prefix).
if misses_after > misses_before or hits_after > hits_before:
    pass_("ThreadWake cache activity observed")
else:
    fail(
        "ThreadWake cache activity observed",
        f"hits before={hits_before} after={hits_after} misses before={misses_before} after={misses_after}",
    )

# ── No request failures ────────────────────────────────────────────────────

if status1 == 200 and status2 == 200:
    pass_("no request failure observed")
else:
    fail("no request failure observed", f"statuses: {status1}, {status2}")

# ── Runtime surfaces do not leak prompt content ────────────────────────────

try:
    status, runtime_data = request_json("GET", "/runtime")
    runtime_str = json.dumps(runtime_data) if status == 200 else ""
except Exception:
    runtime_str = ""

try:
    status, admission_data = request_json("GET", "/runtime/admission")
    admission_str = json.dumps(admission_data) if status == 200 else ""
except Exception:
    admission_str = ""

tw_health_str = json.dumps(tw_after)

# The secret must not appear in any runtime surface.
leaked = False
for surface_name, surface_str in [
    ("/health/threadwake", tw_health_str),
    ("/runtime", runtime_str),
    ("/runtime/admission", admission_str),
]:
    if secret in surface_str:
        fail(f"health surfaces do not leak prompt text ({surface_name})")
        leaked = True

if not leaked:
    pass_("health surfaces do not leak prompt text")

# Token IDs, cache object reprs, model reprs must not leak.
for forbidden in ["token_ids", "PromptCache", "Module", "TokenizerWrapper"]:
    for surface_name, surface_str in [
        ("/health/threadwake", tw_health_str),
        ("/runtime", runtime_str),
    ]:
        if forbidden.lower() in surface_str.lower():
            fail(f"health surfaces do not leak opaque cache internals ({forbidden} in {surface_name})")
            leaked = True

if not leaked:
    pass_("health surfaces do not leak opaque cache internals")

# Generated text must NOT appear in health/runtime.
for text_check in [content1, content2]:
    if text_check and len(text_check) > 3:
        for surface_name, surface_str in [
            ("/health/threadwake", tw_health_str),
            ("/runtime", runtime_str),
            ("/runtime/admission", admission_str),
        ]:
            if text_check in surface_str:
                fail(f"health surfaces do not leak generated text ({surface_name})")
                leaked = True

if not leaked:
    pass_("health surfaces do not leak generated text")

print("")
print("---")
print(f"Passed: {pass_count}  Failed: {fail_count}")

sys.exit(0 if fail_count == 0 else 1)
PY

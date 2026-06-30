#!/bin/sh
# Whoosh'd live FIFO queue smoke test — requires the server to already be running.
#
# Start the server first with queueing and stub delay enabled, for example:
#
#   WHOOSHD_ADAPTER=stub \
#   WHOOSHD_ENABLE_QUEUE=true \
#   WHOOSHD_MAX_ACTIVE_REQUESTS=1 \
#   WHOOSHD_MAX_QUEUE_DEPTH=8 \
#   WHOOSHD_QUEUE_TIMEOUT_SECONDS=10 \
#   WHOOSHD_STUB_RESPONSE_DELAY_SECONDS=2 \
#   python -m uvicorn whooshd.app:app --host 127.0.0.1 --port 8000
#
# Then run:
#
#   sh scripts/smoke_queue_live.sh
#
# Optional:
#   WHOOSHD_BASE_URL=http://127.0.0.1:8000 sh scripts/smoke_queue_live.sh
#   WHOOSHD_QUEUE_SMOKE_MODEL=stub-model sh scripts/smoke_queue_live.sh

BASE_URL="${WHOOSHD_BASE_URL:-http://127.0.0.1:8000}"
MODEL="${WHOOSHD_QUEUE_SMOKE_MODEL:-}"

python3 - "$BASE_URL" "$MODEL" <<'PY'
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
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

secret = f"queue-smoke-secret-{uuid.uuid4().hex[:12]}"
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

def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 10.0):
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

def get_admission():
    status, data = request_json("GET", "/runtime/admission")
    if status != 200:
        raise RuntimeError(f"/runtime/admission returned {status}: {data}")
    return data

def get_requests_raw() -> str:
    conn = http.client.HTTPConnection(host, port, timeout=5.0)
    conn.request("GET", "/runtime/requests")
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return raw

def choose_model() -> str:
    if model_arg:
        return model_arg
    status, data = request_json("GET", "/v1/models")
    if status == 200:
        items = data.get("data") or data.get("models") or []
        if items and isinstance(items[0], dict) and items[0].get("id"):
            return items[0]["id"]
    return "stub-model"

def post_chat(payload: dict, timeout: float = 20.0):
    return request_json("POST", "/v1/chat/completions", payload=payload, timeout=timeout)

print("Whoosh'd Live FIFO Queue Smoke Test")
print(f"  base:  {base_url}")

model = choose_model()
print(f"  model: {model}")
print("")

try:
    before = get_admission()
except Exception as exc:
    fail("/runtime/admission reachable", str(exc))
    print("")
    print("---")
    print(f"Passed: {pass_count}  Failed: {fail_count}")
    sys.exit(1)

if before.get("queue_enabled") is True:
    pass_("queue_enabled is true")
else:
    fail("queue_enabled is true", f"got {before.get('queue_enabled')!r}")

if before.get("max_active_requests") == 1:
    pass_("max_active_requests is 1")
else:
    fail("max_active_requests is 1", f"got {before.get('max_active_requests')!r}")

if before.get("max_queue_depth", 0) >= 1:
    pass_("max_queue_depth allows queued work")
else:
    fail("max_queue_depth allows queued work", f"got {before.get('max_queue_depth')!r}")

if fail_count:
    print("")
    print("Start the server with:")
    print("  WHOOSHD_ENABLE_QUEUE=true")
    print("  WHOOSHD_MAX_ACTIVE_REQUESTS=1")
    print("  WHOOSHD_MAX_QUEUE_DEPTH=8")
    print("  WHOOSHD_STUB_RESPONSE_DELAY_SECONDS=2")
    print("")
    print("---")
    print(f"Passed: {pass_count}  Failed: {fail_count}")
    sys.exit(1)

counters_before = before.get("counters", {})
accepted_before = counters_before.get("accepted", 0)
rejected_before = counters_before.get("rejected", 0)
queued_before = counters_before.get("queued", 0)
dequeued_before = counters_before.get("dequeued", 0)
timeout_before = counters_before.get("queue_timeout", 0)

# Launch blocker in a background thread because the call intentionally sleeps
# inside the running server when WHOOSHD_STUB_RESPONSE_DELAY_SECONDS > 0.
blocker_result = {}
blocker_payload = {
    "model": model,
    "messages": [{"role": "user", "content": f"{secret} blocker request"}],
    "stream": False,
    "max_tokens": 32,
}

def run_blocker():
    try:
        blocker_result["status"], blocker_result["data"] = post_chat(blocker_payload, timeout=30.0)
    except Exception as exc:
        blocker_result["error"] = str(exc)

blocker_thread = threading.Thread(target=run_blocker, daemon=True)
blocker_thread.start()

# Wait until the server observes the blocker as active.
saw_active = False
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    snap = get_admission()
    if snap.get("active_jobs", 0) >= 1:
        saw_active = True
        break
    time.sleep(0.05)

if saw_active:
    pass_("first request became active")
else:
    fail("first request became active", "backend may be too fast; enable stub delay or use a slower runtime")

# Launch second request while first is active. It should queue, then complete
# when the blocker finishes.
queued_result = {}
queued_payload = {
    "model": model,
    "messages": [{"role": "user", "content": f"{secret} queued request"}],
    "stream": False,
    "max_tokens": 32,
}

def run_queued():
    try:
        queued_result["status"], queued_result["data"] = post_chat(queued_payload, timeout=30.0)
    except Exception as exc:
        queued_result["error"] = str(exc)

queued_thread = threading.Thread(target=run_queued, daemon=True)
queued_thread.start()

saw_queued = False
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    snap = get_admission()
    counters = snap.get("counters", {})
    if snap.get("queue_depth", 0) >= 1 or counters.get("queued", 0) > queued_before:
        saw_queued = True
        break
    time.sleep(0.05)

if saw_queued:
    pass_("second request entered FIFO queue")
else:
    fail("second request entered FIFO queue", "queue_depth/counter did not change before blocker finished")

blocker_thread.join(timeout=30.0)
queued_thread.join(timeout=30.0)

if blocker_result.get("status") == 200:
    pass_("first request completed")
else:
    fail("first request completed", str(blocker_result))

if queued_result.get("status") == 200:
    pass_("queued request completed with 200")
else:
    fail("queued request completed with 200", str(queued_result))

after = get_admission()
counters_after = after.get("counters", {})

if counters_after.get("queued", 0) >= queued_before + 1:
    pass_("queued counter incremented")
else:
    fail("queued counter incremented", f"before={queued_before} after={counters_after.get('queued', 0)}")

if counters_after.get("dequeued", 0) >= dequeued_before + 1:
    pass_("dequeued counter incremented")
else:
    fail("dequeued counter incremented", f"before={dequeued_before} after={counters_after.get('dequeued', 0)}")

if counters_after.get("queue_timeout", 0) == timeout_before:
    pass_("queue_timeout counter unchanged")
else:
    fail("queue_timeout counter unchanged", f"before={timeout_before} after={counters_after.get('queue_timeout', 0)}")

if counters_after.get("rejected", 0) == rejected_before:
    pass_("rejected counter unchanged")
else:
    fail("rejected counter unchanged", f"before={rejected_before} after={counters_after.get('rejected', 0)}")

# Wait for active jobs to settle back to zero.
settled = False
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    snap = get_admission()
    if snap.get("active_jobs", 0) == 0 and snap.get("queue_depth", 0) == 0:
        settled = True
        break
    time.sleep(0.05)

if settled:
    pass_("active_jobs and queue_depth returned to zero")
else:
    fail("active_jobs and queue_depth returned to zero", str(get_admission()))

admission_raw = json.dumps(get_admission())
requests_raw = get_requests_raw()
if secret not in admission_raw and secret not in requests_raw:
    pass_("runtime surfaces do not leak prompt content")
else:
    fail("runtime surfaces do not leak prompt content", "secret prompt marker appeared in runtime output")

print("")
print("---")
print(f"Passed: {pass_count}  Failed: {fail_count}")

sys.exit(0 if fail_count == 0 else 1)
PY

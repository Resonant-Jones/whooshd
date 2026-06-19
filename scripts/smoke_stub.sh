#!/bin/sh
# Whoosh'd stub smoke test — requires the server to already be running.
# Start the server first:
#   WHOOSHD_ADAPTER=stub uvicorn whooshd.app:app --port 8000
#
# Then run:
#   sh scripts/smoke_stub.sh

BASE_URL="${WHOOSHD_BASE_URL:-http://127.0.0.1:8000}"
PASS=0
FAIL=0

check() {
    name="$1"
    url="$2"
    expected="$3"
    resp=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    if [ "$resp" = "$expected" ]; then
        echo "  PASS  $name ($resp)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name (expected $expected, got $resp)"
        FAIL=$((FAIL + 1))
    fi
}

echo "Whoosh'd Stub Smoke Test"
echo "  base: $BASE_URL"
echo ""

echo "Liveness & Readiness"
check "/health"         "$BASE_URL/health"         200
check "/ready"          "$BASE_URL/ready"          200

echo ""
echo "Model Inventory"
check "/v1/models"      "$BASE_URL/v1/models"      200
check "/api/tags"       "$BASE_URL/api/tags"       200

echo ""
echo "Chat Completions"
resp=$(curl -s "$BASE_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}]}')
code=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('choices',[{}])[0].get('message',{}).get('content','')[:20])" 2>/dev/null)
if [ -n "$code" ]; then
    echo "  PASS  /v1/chat/completions non-streaming (got response)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  /v1/chat/completions non-streaming (no content)"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "---"
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

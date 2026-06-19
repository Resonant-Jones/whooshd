#!/bin/sh
# Whoosh'd OpenAI compatibility smoke test — requires the server to already be running.
# Start the server first:
#   WHOOSHD_ADAPTER=stub uvicorn whooshd.app:app --port 8000
#
# Then run:
#   sh scripts/smoke_openai_compat.sh

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

echo "Whoosh'd OpenAI Compatibility Smoke Test"
echo "  base: $BASE_URL"
echo ""

echo "Model Inventory"
resp=$(curl -s "$BASE_URL/v1/models" 2>/dev/null)
if echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('object')=='list'; assert len(d.get('data',[]))>0" 2>/dev/null; then
    echo "  PASS  /v1/models returns OpenAI-compatible list"
    PASS=$((PASS + 1))
else
    echo "  FAIL  /v1/models shape mismatch"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "Non-streaming Chat"
resp=$(curl -s "$BASE_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}]}')
if echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert 'id' in d
assert d.get('object')=='chat.completion'
assert len(d.get('choices',[]))>0
assert 'message' in d['choices'][0]
assert 'content' in d['choices'][0]['message']
" 2>/dev/null; then
    echo "  PASS  /v1/chat/completions returns OpenAI-compatible response"
    PASS=$((PASS + 1))
else
    echo "  FAIL  /v1/chat/completions shape mismatch"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "Streaming Chat"
resp=$(curl -s "$BASE_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model":"stub-model","messages":[{"role":"user","content":"Hello"}],"stream":true}')
if echo "$resp" | grep -q "data:" && echo "$resp" | grep -q "\[DONE\]"; then
    echo "  PASS  /v1/chat/completions streaming (SSE with [DONE])"
    PASS=$((PASS + 1))
else
    echo "  FAIL  /v1/chat/completions streaming (missing data: or [DONE])"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "---"
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

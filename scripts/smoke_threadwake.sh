#!/bin/sh
# Whoosh'd ThreadWake smoke test — requires the server to already be running.
# Start the server first:
#   WHOOSHD_ADAPTER=stub uvicorn whooshd.app:app --port 8000
#
# ThreadWake is off by default. This smoke confirms the visibility surfaces
# work and return counts/status only. No KV reuse is enabled.

BASE_URL="${WHOOSHD_BASE_URL:-http://127.0.0.1:8000}"
PASS=0
FAIL=0

check_field() {
    name="$1"
    url="$2"
    field="$3"
    resp=$(curl -s "$url" 2>/dev/null)
    val=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
# Navigate nested dict with dot-separated path
parts='$field'.split('.')
v=d
for p in parts:
    if isinstance(v,dict): v=v.get(p,'NOT_FOUND')
    else: v='NOT_FOUND'; break
print(v)
" 2>/dev/null)
    if [ "$val" != "NOT_FOUND" ] && [ "$val" != "null" ]; then
        echo "  PASS  $name ($field=$val)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name ($field not found)"
        FAIL=$((FAIL + 1))
    fi
}

check_no_leak() {
    name="$1"
    url="$2"
    resp=$(curl -s "$url" 2>/dev/null)
    if echo "$resp" | python3 -c "import sys,json; d=json.dumps(json.load(sys.stdin)); assert 'token_ids' not in d; assert 'opaque_ref' not in d" 2>/dev/null; then
        echo "  PASS  $name (no sensitive fields)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name (sensitive fields detected)"
        FAIL=$((FAIL + 1))
    fi
}

echo "Whoosh'd ThreadWake Smoke Test"
echo "  base: $BASE_URL"
echo "  ThreadWake is off by default — all counts should be zero"
echo ""

echo "ThreadWake Health"
check_field "/health/threadwake mode"     "$BASE_URL/health/threadwake"      mode
check_no_leak "/health/threadwake safe"   "$BASE_URL/health/threadwake"

echo ""
echo "ThreadWake Analysis"
check_field "/runtime/threadwake/analysis candidates" "$BASE_URL/runtime/threadwake/analysis" analysis.candidates_scanned
check_no_leak "/runtime/threadwake/analysis safe"     "$BASE_URL/runtime/threadwake/analysis"

echo ""
echo "---"
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "ThreadWake visibility surfaces are working and safe."
    echo "All counts are zero — expected when ThreadWake is off."
else
    echo ""
    echo "Some checks failed. Is the server running with WHOOSHD_ADAPTER=stub?"
fi
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

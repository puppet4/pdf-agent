#!/usr/bin/env bash
# E2E validation script for PDF Agent.
# Prerequisites: docker compose up -d, OPENAI_API_KEY set.
#
# Usage:
#   export OPENAI_API_KEY=sk-...
#   docker compose up -d
#   bash scripts/test_e2e.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
TIMEOUT=30

echo "=== PDF Agent Conversation E2E Validation ==="

# 1. Health check
echo -n "[1/5] Health check... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/healthz")
if [ "$HTTP_CODE" = "200" ]; then
    echo "OK"
else
    echo "FAIL (HTTP $HTTP_CODE)"
    exit 1
fi

# 2. Upload a test PDF
echo -n "[2/4] File upload... "
# Create a minimal PDF
TMPFILE=$(mktemp /tmp/test_XXXX.pdf)
python3 -c "
import pikepdf
pdf = pikepdf.Pdf.new()
for i in range(3):
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)
pdf.save('$TMPFILE')
"
UPLOAD_RESP=$(curl -s -F "file=@$TMPFILE;type=application/pdf" "$BASE_URL/api/files")
FILE_ID=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK (file_id=$FILE_ID)"

# 3. Agent chat (SSE stream)
echo -n "[3/4] Agent chat (SSE)... "
SSE_OUTPUT=$(curl -s -N --max-time "$TIMEOUT" \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"Please tell me the metadata info of this PDF.\", \"file_ids\": [\"$FILE_ID\"]}" \
    "$BASE_URL/api/agent/chat" 2>&1 || true)

if echo "$SSE_OUTPUT" | grep -q "event: thread"; then
    echo "OK (received conversation SSE events)"
else
    echo "FAIL (no thread event)"
    echo "$SSE_OUTPUT" | head -20
    exit 1
fi

# Extract thread_id
THREAD_ID=$(echo "$SSE_OUTPUT" | grep "event: thread" -A1 | grep "data:" | head -1 | python3 -c "import sys,json; print(json.loads(sys.stdin.readline().replace('data: ',''))['thread_id'])" 2>/dev/null || echo "")

if echo "$SSE_OUTPUT" | grep -q "event: done"; then
    echo "  -> Stream completed successfully"
else
    echo "  -> Warning: stream may not have completed"
fi

# 4. Check for processing step or completion
echo -n "[4/4] Processing check... "
if echo "$SSE_OUTPUT" | grep -q "event: tool_start"; then
    TOOL_NAME=$(echo "$SSE_OUTPUT" | grep "event: tool_start" -A1 | grep "data:" | head -1 | python3 -c "import sys,json; print(json.loads(sys.stdin.readline().replace('data: ',''))['tool'])" 2>/dev/null || echo "unknown")
    echo "OK (step=$TOOL_NAME)"
elif echo "$SSE_OUTPUT" | grep -q "event: done"; then
    echo "OK (conversation completed without explicit tool event)"
else
    echo "FAIL (no completion event)"
    exit 1
fi

# Cleanup
rm -f "$TMPFILE"

echo ""
echo "=== E2E Validation Complete ==="
if [ -n "$THREAD_ID" ]; then
    echo "Conversation ID: $THREAD_ID"
    echo "List result files: curl $BASE_URL/api/agent/threads/$THREAD_ID/files"
fi

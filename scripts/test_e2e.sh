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
API_KEY="${PDF_AGENT_API_KEY:-${API_KEY:-}}"
TIMEOUT=30
AUTH_HEADER_ARGS=()
if [ -n "$API_KEY" ]; then
    AUTH_HEADER_ARGS=(-H "X-API-Key: $API_KEY")
fi

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
UPLOAD_RESP=$(curl -sS "${AUTH_HEADER_ARGS[@]}" -F "file=@$TMPFILE;type=application/pdf" "$BASE_URL/api/files")
FILE_ID=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK (file_id=$FILE_ID)"

echo -n "[3/4] Conversation create... "
CONVERSATION_RESP=$(curl -sS "${AUTH_HEADER_ARGS[@]}" -X POST "$BASE_URL/api/conversations")
CONVERSATION_ID=$(echo "$CONVERSATION_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "OK (conversation_id=$CONVERSATION_ID)"

# 4. Conversation message (SSE stream)
echo -n "[4/5] Conversation message (SSE)... "
SSE_OUTPUT=$(curl -s -N --max-time "$TIMEOUT" \
    "${AUTH_HEADER_ARGS[@]}" \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"Please tell me the metadata info of this PDF.\", \"file_ids\": [\"$FILE_ID\"]}" \
    "$BASE_URL/api/conversations/$CONVERSATION_ID/messages" 2>&1 || true)

if echo "$SSE_OUTPUT" | grep -q "event: conversation"; then
    echo "OK (received conversation SSE events)"
else
    echo "FAIL (no conversation event)"
    echo "$SSE_OUTPUT" | head -20
    exit 1
fi

if echo "$SSE_OUTPUT" | grep -q "event: done"; then
    echo "  -> Stream completed successfully"
else
    echo "  -> Warning: stream may not have completed"
fi

# 5. Check for processing step or completion
echo -n "[5/5] Processing check... "
if echo "$SSE_OUTPUT" | grep -q "event: tool_start"; then
    TOOL_NAME=$(echo "$SSE_OUTPUT" | grep "event: tool_start" -A1 | grep "data:" | head -1 | python3 -c "import sys,json; print(json.loads(sys.stdin.readline().replace('data: ',''))['name'])" 2>/dev/null || echo "unknown")
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
echo "Conversation ID: $CONVERSATION_ID"
echo "List result files: curl ${API_KEY:+-H \"X-API-Key: $API_KEY\" }$BASE_URL/api/conversations/$CONVERSATION_ID/artifacts"

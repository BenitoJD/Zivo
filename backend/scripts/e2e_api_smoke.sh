#!/usr/bin/env bash
# End-to-end API smoke — exercises all major backend flows.
set -euo pipefail

API="${API_BASE:-http://127.0.0.1:3000}"
COOKIE_JAR="$(mktemp)"
TMPDIR="${TMPDIR:-/tmp}"
PDF="$TMPDIR/zivo-e2e-test.pdf"
PASTE_TEXT="Mitochondria produce ATP via cellular respiration. The electron transport chain drives ATP synthase."

cleanup() { rm -f "$COOKIE_JAR" "$PDF"; }
trap cleanup EXIT

# minimal PDF
python3 -c "
pdf = b'''%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj
4 0 obj<</Length 44>>stream
BT /F1 24 Tf 100 700 Td (E2E Test Page) Tj ET
endstream endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000214 00000 n 
trailer<</Size 5/Root 1 0 R>>
startxref
312
%%EOF'''
open('$PDF','wb').write(pdf)
"

pass() { echo "✓ $1"; }
fail() { echo "✗ $1"; exit 1; }

echo "=== Health ==="
curl -fsS "$API/api/health" | grep -q '"status":"ok"' && pass health || fail health

echo "=== Guest session ==="
GUEST_JSON=$(curl -fsS -c "$COOKIE_JAR" -X POST "$API/api/guest" -H 'Content-Type: application/json' -d '{}')
echo "$GUEST_JSON" | grep -q guest_id && pass guest || fail guest

echo "=== Signup (multi-doc tests) ==="
USER="e2e$(date +%s)"
SIGNUP=$(curl -fsS -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$API/api/auth/signup" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"testpass123\",\"confirm_password\":\"testpass123\",\"accept_terms\":true}")
echo "$SIGNUP" | grep -q csrf_token && pass signup || fail signup
CSRF=$(echo "$SIGNUP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('csrf_token',''))")

echo "=== PDF upload ==="
PDF_RES=$(curl -fsS -b "$COOKIE_JAR" -X POST "$API/api/sources" \
  -H "X-CSRF-Token: $CSRF" \
  -F "file=@$PDF;type=application/pdf;filename=e2e-test.pdf")
PDF_ID=$(echo "$PDF_RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
[[ -n "$PDF_ID" ]] && pass "pdf upload $PDF_ID" || fail pdf

echo "=== Paste import ==="
PASTE_RES=$(curl -fsS -b "$COOKIE_JAR" -X POST "$API/api/sources/import-text" \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -d "{\"text\":$(python3 -c "import json; print(json.dumps('$PASTE_TEXT'))")}")
PASTE_ID=$(echo "$PASTE_RES" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
[[ -n "$PASTE_ID" ]] && pass "paste import $PASTE_ID" || fail paste

echo "=== List sources ==="
SRC=$(curl -fsS -b "$COOKIE_JAR" "$API/api/sources")
echo "$SRC" | grep -q "$PASTE_ID" && pass sources_list || fail sources_list

echo "=== Page range (paste) ==="
curl -fsS -b "$COOKIE_JAR" -X POST "$API/api/artifacts/$PASTE_ID/page-range" \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"from":1,"to":1}' | grep -q job_id && pass page_range || fail page_range

echo "=== Wait indexing (paste) ==="
for i in $(seq 1 40); do
  ST=$(curl -fsS -b "$COOKIE_JAR" "$API/api/artifacts/$PASTE_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [[ "$ST" == "ready" ]] && break
  [[ "$ST" == "failed" ]] && fail "paste indexing failed"
  sleep 2
done
[[ "$ST" == "ready" ]] && pass "paste ready" || fail "paste not ready: $ST"

echo "=== Learn queue (paste) ==="
for i in $(seq 1 90); do
  LQ=$(curl -fsS -b "$COOKIE_JAR" "$API/api/artifacts/$PASTE_ID/learn-queue")
  AID=$(echo "$LQ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current_assertion_id') or '')")
  [[ -n "$AID" && "$AID" != "None" ]] && break
  sleep 3
done
[[ -n "$AID" ]] && pass "learn queue assertion $AID" || fail "no assertion in learn queue"

echo "=== Assertion payload ==="
curl -fsS -b "$COOKIE_JAR" "$API/api/assertions/$AID" | grep -q question && pass assertion || fail assertion

echo "=== MCQ grade ==="
OPTS=$(curl -fsS -b "$COOKIE_JAR" "$API/api/assertions/$AID" | python3 -c "
import sys,json
p=json.load(sys.stdin)['payload']
opts=p.get('options') or p.get('choices') or []
ci=int(p.get('correct_index',0))
print(json.dumps({'assertion_id':'$AID','choice_index':ci}))
")
curl -fsS -b "$COOKIE_JAR" -X POST "$API/api/mcq/grade" -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -d "$OPTS" | grep -q correct && pass grade || fail grade

echo "=== Chat thread ==="
curl -fsS -b "$COOKIE_JAR" "$API/api/chat/threads/$PASTE_ID/messages" | grep -q '\[' && pass chat_messages || fail chat_messages

echo "=== Chat send (SSE) ==="
CHAT_HTTP=$(curl -s -o /tmp/zivo-chat.out -w '%{http_code}' -b "$COOKIE_JAR" -X POST "$API/api/chat" \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d "{\"document_id\":\"$PASTE_ID\",\"message\":\"What is ATP?\"}")
[[ "$CHAT_HTTP" == "200" ]] && grep -q event /tmp/zivo-chat.out && pass chat_send || fail "chat_send http=$CHAT_HTTP"

echo "=== Session ==="
curl -fsS -b "$COOKIE_JAR" "$API/api/auth/session" | grep -q "$USER" && pass session || fail session

echo "=== PDF page-range + indexing ==="
curl -fsS -b "$COOKIE_JAR" -X POST "$API/api/artifacts/$PDF_ID/page-range" \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"from":1,"to":1}' | grep -q job_id && pass pdf_page_range || fail pdf_page_range
for i in $(seq 1 40); do
  PST=$(curl -fsS -b "$COOKIE_JAR" "$API/api/artifacts/$PDF_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [[ "$PST" == "ready" ]] && break
  sleep 2
done
[[ "$PST" == "ready" ]] && pass pdf_ready || fail pdf_ready

echo "=== Delete paste source ==="
curl -fsS -b "$COOKIE_JAR" -X DELETE "$API/api/sources/$PASTE_ID" -H "X-CSRF-Token: $CSRF" -o /dev/null -w '%{http_code}' | grep -q 204 && pass delete || fail delete

echo ""
echo "ALL API E2E CHECKS PASSED"

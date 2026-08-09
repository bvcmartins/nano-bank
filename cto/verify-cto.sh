#!/usr/bin/env bash
set -euo pipefail
# End-to-end CTO smoke. Prereqs (port-forward or run in-cluster):
#   - platform MCP :8094  (reads both clusters via the mounted kubeconfig)
#   - CTO API :8095       (OLLAMA_API_KEY=… python -m cto.api_main)
CTO="${CTO_API_URL:-http://localhost:8095}"

echo "== CTO health =="
curl -fsS "$CTO/health" | tee /dev/stderr | grep -q '"status":"ok"'

echo "== ask the CTO for an estate health review =="
RESP=$(curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d '{"message":"Give me a platform health review right now: deployment health, any crashlooping pods, rollout status and image/version drift across both clusters, with the numbers."}')
ANSWER=$(echo "$RESP" | python -c 'import sys,json; print(json.load(sys.stdin)["answer"])')
echo "$ANSWER"
echo "$ANSWER" | grep -Eq '[0-9]' || { echo "FAIL: no figures in CTO answer"; exit 1; }

echo "== figures are tool-grounded (empty ungrounded list) =="
echo "$RESP" | python -c 'import sys,json; v=json.load(sys.stdin)["verification"]; \
print("REVISED", v["revised"], "UNGROUNDED", v["ungrounded"]); \
sys.exit(0 if v["ungrounded"]==[] else 1)' \
  || { echo "FAIL: CTO answer has ungrounded figures"; exit 1; }

echo "== the harness planned and used todos =="
echo "$RESP" | python -c 'import sys,json; t=json.load(sys.stdin)["trace"]; \
names=[e.get("name") for e in t]; \
assert "write_plan" in names, "no write_plan"; assert "write_todos" in names, "no write_todos"; \
print("harness: planned + todos OK")' \
  || { echo "FAIL: CTO did not plan / use todos"; exit 1; }

echo "== defer a books (NIM) question to the CFO =="
PUSHBACK=$(curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d '{"message":"What is our net interest margin trending at?"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["answer"])')
echo "$PUSHBACK"
echo "$PUSHBACK" | grep -Eiq "CFO|out of (my )?scope|do(es)? not (have|show|track|cover)|can(no|'?)t" \
  || { echo "FAIL: CTO engaged an out-of-lane books premise"; exit 1; }

echo "CTO SMOKE PASSED"

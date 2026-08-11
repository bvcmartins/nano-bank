#!/bin/bash
# Live smoke for the CTO infra levers. With the CTO stack up, induce a crashloop
# on an allow-listed app, ask the CTO to handle it, and confirm (a) the CTO
# actually pulled execute_rollout_restart and (b) a fresh actor='cto' row landed
# in the hash-chained ledger with the chain still intact.
# Requires: kubectl (kind-nano-bank) + a CTO API reachable at $CTO_API_URL (:8095).
set -euo pipefail
CTX=kind-nano-bank; NS=nano-bank; DEPLOY="${DEPLOY:-cfo}"
CTO="${CTO_API_URL:-http://localhost:8095}"
PSQL=(kubectl --context "$CTX" -n "$NS" exec deploy/postgres -- \
      psql -U nanobank_user -d nano_bank_db -tAc)

echo "== break $DEPLOY (bad command -> crashloop) =="
kubectl --context "$CTX" -n "$NS" patch deploy/"$DEPLOY" --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["/bin/false"]}]'
kubectl --context "$CTX" -n "$NS" rollout status deploy/"$DEPLOY" --timeout=40s || true

echo "== ask the CTO to handle it =="
RESP=$(curl -fsS -XPOST "$CTO/ask" -H 'content-type: application/json' \
  -d "{\"message\":\"$DEPLOY looks crashlooping — investigate and, if warranted, restart it.\"}")
echo "$RESP" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("ANSWER:", d["answer"])
assert "execute_rollout_restart" in json.dumps(d.get("trace", [])), \
    "CTO did not pull the lever"
print("OK: CTO pulled execute_rollout_restart")
'

echo "== ledger shows the actor=cto entry =="
"${PSQL[@]}" \
  "SELECT actor||' | '||action||' | '||COALESCE(effect->>'outcome','?') \
   FROM agent_action_ledger WHERE actor='cto' ORDER BY seq DESC LIMIT 3;"

echo "== chain intact? verify_agent_ledger() returns the seq of the first bad row =="
BAD=$("${PSQL[@]}" "SELECT COALESCE(verify_agent_ledger()::text, 'INTACT');")
echo "verify_agent_ledger -> $BAD"
test "$BAD" = "INTACT"

echo "== restore $DEPLOY =="
kubectl --context "$CTX" -n "$NS" patch deploy/"$DEPLOY" --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' || true
kubectl --context "$CTX" -n "$NS" rollout status deploy/"$DEPLOY" --timeout=120s || true
echo "SMOKE DONE"

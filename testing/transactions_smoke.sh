#!/usr/bin/env bash
# End-to-end smoke test for the transaction endpoints.
#
# Drives: create customer -> two accounts -> deposit -> transfer -> withdrawal
# -> history, asserting balances and a couple of error paths along the way.
#
# Requires a running stack (see repo CLAUDE.md):
#   - Kind Postgres (::1:5432, port-forwarded)
#   - one ledger core (modern :8091 or legacy :8090)
#   - the API:  cd api && cargo run           (:8081)
#
# Deposit/withdrawal post to the GL core; if no core is up they return 503 and
# this script fails fast.
#
# Usage:  BASE_URL=http://localhost:8081 testing/transactions_smoke.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8081}"
PASS=0
FAIL=0

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }
need curl
need jq

# post PATH JSON -> sets $BODY and $CODE
req() {
  local method="$1" path="$2" data="${3:-}"
  local out
  if [[ -n "$data" ]]; then
    out=$(curl -sS -w '\n%{http_code}' -X "$method" "$BASE_URL$path" \
      -H 'content-type: application/json' -d "$data")
  else
    out=$(curl -sS -w '\n%{http_code}' -X "$method" "$BASE_URL$path")
  fi
  CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
}

check() { # check DESC EXPECTED ACTUAL
  if [[ "$2" == "$3" ]]; then
    echo "  ✅ $1"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $1 (expected '$2', got '$3')"
    echo "     body: $BODY"
    FAIL=$((FAIL + 1))
  fi
}

bal() { # echo balance of account $1
  req GET "/api/v1/accounts/$1/balance"
  echo "$BODY" | jq -r '.balance'
}

echo "▶ nano-bank transactions smoke test @ $BASE_URL"

req GET "/health"
[[ "$CODE" == "200" ]] || { echo "API not reachable at $BASE_URL (health=$CODE)"; exit 1; }

# --- setup: customer + two deposit accounts ---
RID=$RANDOM$RANDOM
req POST "/api/v1/customers" "$(jq -n --arg e "smoke_${RID}@example.com" --arg p "$(printf '%010d' $((RID % 10000000000)))" --arg s "$(printf '%09d' $((RID % 1000000000)))" \
  '{email:$e, phone_number:$p, first_name:"Smoke", last_name:"Test", date_of_birth:"1990-01-01", sin:$s, password:"securepass123"}')"
check "create customer" "201" "$CODE"
CUST=$(echo "$BODY" | jq -r '.customer_id')

req POST "/api/v1/accounts" "$(jq -n --arg c "$CUST" '{customer_id:$c, account_type:"chequing"}')"
check "open chequing A" "201" "$CODE"
A=$(echo "$BODY" | jq -r '.account_id')

req POST "/api/v1/accounts" "$(jq -n --arg c "$CUST" '{customer_id:$c, account_type:"savings"}')"
check "open savings B" "201" "$CODE"
B=$(echo "$BODY" | jq -r '.account_id')

# --- deposit 1000 into A ---
req POST "/api/v1/transactions/deposit" "$(jq -n --arg a "$A" '{account_id:$a, amount:1000.00, description:"payday"}')"
if [[ "$CODE" == "503" ]]; then
  echo "  ⚠  GL core unavailable (deposit 503) — start a core (:8091/:8090). Aborting."
  exit 1
fi
check "deposit 1000 -> A" "201" "$CODE"
check "A balance after deposit" "1000.00" "$(bal "$A")"

# --- transfer 400 A -> B ---
req POST "/api/v1/transactions/transfer" "$(jq -n --arg a "$A" --arg b "$B" '{from_account_id:$a, to_account_id:$b, amount:400.00, description:"rent"}')"
check "transfer 400 A -> B" "201" "$CODE"
check "A balance after transfer" "600.00" "$(bal "$A")"
check "B balance after transfer" "400.00" "$(bal "$B")"

# --- withdraw 100 from A ---
req POST "/api/v1/transactions/withdrawal" "$(jq -n --arg a "$A" '{account_id:$a, amount:100.00, description:"atm"}')"
check "withdraw 100 <- A" "201" "$CODE"
check "A balance after withdrawal" "500.00" "$(bal "$A")"

# --- error paths ---
req POST "/api/v1/transactions/withdrawal" "$(jq -n --arg a "$A" '{account_id:$a, amount:99999.00, description:"too much"}')"
check "overdraw rejected (400)" "400" "$CODE"
check "  -> INSUFFICIENT_FUNDS" "INSUFFICIENT_FUNDS" "$(echo "$BODY" | jq -r '.error.code')"

req POST "/api/v1/transactions/transfer" "$(jq -n --arg a "$A" '{from_account_id:$a, to_account_id:$a, amount:1.00, description:"self"}')"
check "self-transfer rejected (400)" "400" "$CODE"

# --- history ---
req GET "/api/v1/transactions?account_id=$A"
check "history for A (200)" "200" "$CODE"
check "  -> 3 transactions" "3" "$(echo "$BODY" | jq -r '.total_count')"
check "  -> newest is withdrawal" "withdrawal" "$(echo "$BODY" | jq -r '.transactions[0].transaction_type')"
check "  -> entries hydrated (2 legs)" "2" "$(echo "$BODY" | jq -r '.transactions[0].entries | length')"

echo
echo "▶ done: $PASS passed, $FAIL failed"
[[ "$FAIL" == "0" ]]

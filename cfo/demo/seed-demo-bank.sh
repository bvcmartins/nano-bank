#!/usr/bin/env bash
# Seed a realistic demo bank so the Agent CFO has a real balance sheet to talk
# about. Two phases, because a bank's month starts with a balance sheet:
#
#   Phase A — the treasury desk builds the opening balance sheet, which is
#             closed as the PRIOR period. Without an opening snapshot every
#             average (earning assets, deposits) is halved and NIM / cost of
#             funds come out roughly double.
#   Phase B — the month happens: retail customers transact, the card rails run,
#             interest is earned and paid, batches accrue and capitalise. Closed
#             as the CURRENT period.
#
# Capital structure is deliberately bank-like: ~8.6% equity / assets, funded
# mostly by deposits. Interest amounts are derived from annual rates over the
# real day count (ACT/365), so the resulting yields and spreads are honest.
#
# The GL is reset and repopulated on every run so the demo is reproducible —
# re-seeding on top of a previous run would compound the opening book. Pass
# --keep-gl to skip the reset and post on top of whatever is already there.
#
# Usage:
#   bash cfo/demo/seed-demo-bank.sh            # reset the GL, then seed
#   bash cfo/demo/seed-demo-bank.sh --keep-gl  # seed on top of the current GL
#
# Prereqs: bank API on :8081 and the finance venv (finance/.venv).
set -euo pipefail

API="${API:-http://localhost:8081}"
SERVICE_SECRET="${SERVICE_SECRET:-nano-bank-visa-network-secret-change-me}"
PERIOD="${PERIOD:-$(date +%Y-%m)}"
CUSTOMERS="${CUSTOMERS:-5}"
ACCRUAL_DAYS="${ACCRUAL_DAYS:-10}"
RESET=1; [ "${1:-}" = "--keep-gl" ] && RESET=0
PW="demopass123"
TAG="cfodemo$(date +%s)"

cd "$(dirname "$0")/../.."
source finance/.venv/bin/activate

# ── the bank we are building ────────────────────────────────────────────────
# Calibrated so the resulting ratios sit in real-bank territory: ~9% equity /
# assets, loan-to-deposit ~73%, efficiency ~60%, NIM ~5.4% (card-heavy, like a
# consumer challenger), RWA capital ratio ~15%, RAROC ~15%.
CAPITAL=80000.00          # shareholder equity
DEPOSITS=700000.00        # wholesale + retail deposit funding
TREASURY=150000.00        # govt bills           @ 4.50%
LOANS=400000.00           # consumer loan book   @ 7.50%
CARDS=90000.00            # card receivables     @ 19.99%
OVERDRAFT=20000.00        # overdraft book       @ 21.00%
RESERVES=120000.00        # = CAPITAL + DEPOSITS - the earning assets
OPEX=2136.00              # staff + technology for the month

PRIOR=$(python3 -c "y,m=map(int,'$PERIOD'.split('-')); print(f'{y-1}-12' if m==1 else f'{y}-{m-1:02d}')")
DAYS=$(python3 -c "import calendar; y,m=map(int,'$PERIOD'.split('-')); print(calendar.monthrange(y,m)[1])")
# ACT/365 interest for the month, from an annual rate.
acct() { python3 -c "print(f'{$1 * $2 * $DAYS / 365:.2f}')"; }

jget() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }
step() { printf '\n\033[0;36m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[0;32m✓\033[0m %s\n' "$*"; }

journal() { # <description> <lines-json>
  curl -fsS -XPOST "$API/api/v1/ledger/journal" -H 'content-type: application/json' \
    -d "{\"description\":\"$1\",\"lines\":$2}" >/dev/null
  ok "$1"
}
leg() { printf '{"account":"%s","direction":"%s","amount":%s}' "$1" "$2" "$3"; }

close_period() { # <period>
  NANO_BANK_API="$API" python - "$1" <<'PY'
import sys
from finance.config import Settings
from finance.db import FinanceDB
from finance import ledger_client, snapshots
period = sys.argv[1]
s = Settings.from_env(); db = FinanceDB(s.db); db.ensure_schema()
out = snapshots.close_period(period, ledger_client.get_balances(s.nano_bank_api), db)
print(f"   closed {period}: {out.get('roles_captured', out)} roles")
PY
}

step "0/8  health + service token"
curl -fsS "$API/health" >/dev/null
SVC=$(curl -fsS -XPOST "$API/api/v1/auth/service-token" -H 'content-type: application/json' \
  -d "{\"client_secret\":\"$SERVICE_SECRET\"}" | jget "['access_token']")
ok "bank API reachable at $API — building $PERIOD ($DAYS days, opening $PRIOR)"

if [ "$RESET" = 1 ]; then
  # --yes: the caller already opted into a GL reset by not passing --keep-gl,
  # and stdin here is a pipe, so reset-gl.sh's own confirm prompt can't read it.
  bash cfo/demo/reset-gl.sh --yes | sed 's/^/   /'
  ok "GL reset — the opening book below is the whole balance sheet"
else
  ok "--keep-gl: posting on top of the existing GL"
fi

# ══ PHASE A — the opening balance sheet ═════════════════════════════════════
step "1/8  treasury desk — opening balance sheet"
journal "shareholder capital" \
  "[$(leg cash_reserves debit $CAPITAL),$(leg capital credit $CAPITAL)]"
journal "deposit funding base" \
  "[$(leg cash_reserves debit $DEPOSITS),$(leg customer_deposits credit $DEPOSITS)]"
journal "treasury placement — govt bills" \
  "[$(leg treasury_placement debit $TREASURY),$(leg cash_reserves credit $TREASURY)]"
journal "consumer loan book" \
  "[$(leg loans_receivable debit $LOANS),$(leg cash_reserves credit $LOANS)]"
journal "card receivable book" \
  "[$(leg card_receivable debit $CARDS),$(leg cash_reserves credit $CARDS)]"
journal "overdraft book" \
  "[$(leg overdraft_receivable debit $OVERDRAFT),$(leg cash_reserves credit $OVERDRAFT)]"

step "2/8  close the opening period ($PRIOR)"
close_period "$PRIOR"
ok "opening balance sheet on the books — averages will be computed against it"

# ══ PHASE B — the month ═════════════════════════════════════════════════════
step "3/8  retail customers, accounts and transactions"
declare -a EMAILS=() TOKENS=() CHEQ=() CARDACCTS=()
for i in $(seq 1 "$CUSTOMERS"); do
  N=$((RANDOM * 32768 + RANDOM))
  EMAIL="${TAG}_${i}@example.com"
  curl -fsS -XPOST "$API/api/v1/customers" -H 'content-type: application/json' -d "{
    \"email\":\"$EMAIL\",\"phone_number\":\"$(printf '%010d' $((N % 10000000000)))\",
    \"first_name\":\"Demo\",\"last_name\":\"Customer$i\",\"date_of_birth\":\"1988-04-1$((i % 9))\",
    \"sin\":\"$(printf '%09d' $((N % 1000000000)))\",\"password\":\"$PW\"}" >/dev/null
  TOK=$(curl -fsS -XPOST "$API/api/v1/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" | jget "['access_token']")

  mkacct() {
    curl -fsS -XPOST "$API/api/v1/accounts" -H "authorization: Bearer $TOK" \
      -H 'content-type: application/json' -d "{\"account_type\":\"$1\"}" | jget "['account_id']"
  }
  C=$(mkacct chequing); S=$(mkacct savings); K=$(mkacct credit_card)

  tx() { curl -fsS -XPOST "$API/api/v1/transactions/$1" -H "authorization: Bearer $TOK" \
           -H 'content-type: application/json' -d "$2" >/dev/null; }
  tx deposit    "{\"account_id\":\"$C\",\"amount\":$((2400 + i * 350)).00,\"description\":\"payroll\"}"
  tx deposit    "{\"account_id\":\"$S\",\"amount\":$((1800 + i * 600)).00,\"description\":\"savings\"}"
  tx withdrawal "{\"account_id\":\"$C\",\"amount\":$((120 + i * 30)).00,\"description\":\"cash\"}"
  tx transfer   "{\"from_account_id\":\"$C\",\"to_account_id\":\"$S\",\"amount\":$((200 + i * 50)).00,\"description\":\"to savings\"}"

  EMAILS+=("$EMAIL"); TOKENS+=("$TOK"); CHEQ+=("$C"); CARDACCTS+=("$K")
  ok "customer $i — chequing/savings/credit-card funded and active"
done

step "4/8  card rails — purchases through authorize/capture, then settlement"
MERCHANTS=("Loblaws" "Tim Hortons" "Petro-Canada" "Indigo" "Canadian Tire")
for idx in "${!CARDACCTS[@]}"; do
  for j in 1 2 3; do
    AMT=$(( (idx + 1) * 55 + j * 34 ))
    AUTH=$(curl -fsS -XPOST "$API/api/v1/cards/authorize" -H "authorization: Bearer $SVC" \
      -H 'content-type: application/json' \
      -d "{\"account_id\":\"${CARDACCTS[$idx]}\",\"amount\":${AMT}.00,\"merchant\":\"${MERCHANTS[$idx]}\"}" \
      | jget "['auth_id']")
    curl -fsS -XPOST "$API/api/v1/cards/capture" -H "authorization: Bearer $SVC" \
      -H 'content-type: application/json' -d "{\"auth_id\":\"$AUTH\"}" >/dev/null
  done
done
curl -fsS -XPOST "$API/api/v1/cards/settle" -H "authorization: Bearer $SVC" >/dev/null
ok "$(( ${#CARDACCTS[@]} * 3 )) purchases captured and settled (interchange recognized)"

step "5/8  Interac e-Transfers"
SENT=0
for idx in 0 1 2; do
  curl -fsS -XPOST "$API/api/v1/interac/etransfers" -H "authorization: Bearer ${TOKENS[$idx]}" \
    -H 'content-type: application/json' -d "{
      \"from_account_id\":\"${CHEQ[$idx]}\",\"amount\":$(( 60 + idx * 45 )).00,
      \"recipient_handle_type\":\"email\",\"recipient_handle_value\":\"${EMAILS[$((idx + 1))]}\",
      \"security_question\":\"City of birth?\",\"security_answer\":\"calgary\",
      \"memo\":\"rent split\"}" >/dev/null 2>&1 && SENT=$((SENT + 1)) || true
done
ok "$SENT e-Transfer(s) sent (fee income recognized)"

step "6/8  bank P&L for $PERIOD — ACT/365 on the opening book"
journal "interest earned — treasury placements @ 4.50%" \
  "[$(leg cash_reserves debit "$(acct $TREASURY 0.0450)"),$(leg interest_income credit "$(acct $TREASURY 0.0450)")]"
journal "interest earned — consumer loans @ 7.50%" \
  "[$(leg cash_reserves debit "$(acct $LOANS 0.0750)"),$(leg interest_income credit "$(acct $LOANS 0.0750)")]"
journal "interest earned — card book @ 19.99%" \
  "[$(leg cash_reserves debit "$(acct $CARDS 0.1999)"),$(leg interest_income credit "$(acct $CARDS 0.1999)")]"
journal "interest earned — overdrafts @ 21.00%" \
  "[$(leg cash_reserves debit "$(acct $OVERDRAFT 0.2100)"),$(leg interest_income credit "$(acct $OVERDRAFT 0.2100)")]"
journal "funding cost — deposits @ 2.50%" \
  "[$(leg interest_expense debit "$(acct $DEPOSITS 0.0250)"),$(leg cash_reserves credit "$(acct $DEPOSITS 0.0250)")]"
journal "operating expense — staff and technology" \
  "[$(leg operating_expense debit $OPEX),$(leg cash_reserves credit $OPEX)]"

step "7/8  finance batches — daily accrual x${ACCRUAL_DAYS}, then capitalisation"
for d in $(seq "$ACCRUAL_DAYS" -1 1); do
  ASOF=$(date -d "-$d day" +%F)
  curl -fsS -XPOST "$API/api/v1/finance/accrue" -H "authorization: Bearer $SVC" \
    -H 'content-type: application/json' -d "{\"as_of\":\"$ASOF\"}" >/dev/null || true
done
ok "accrued interest for the last ${ACCRUAL_DAYS} days"
curl -fsS -XPOST "$API/api/v1/finance/capitalise" -H "authorization: Bearer $SVC" \
  -H 'content-type: application/json' -d "{\"period\":\"$PERIOD\"}" >/dev/null || true
ok "capitalised $PERIOD (deposit/card interest + maintenance fees)"

step "8/8  close $PERIOD"
close_period "$PERIOD"

printf '\n\033[0;32mDemo bank seeded.\033[0m Ask the CFO about %s (opening book: %s).\n' "$PERIOD" "$PRIOR"

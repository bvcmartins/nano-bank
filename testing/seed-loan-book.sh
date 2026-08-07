#!/usr/bin/env bash
# DEMO / TEST ONLY. Seed a synthetic loan book into the GL so CFO reviews show a
# believable NIM / RAROC. nano-bank has no loan product (accrual only runs on
# deposits + credit cards), so a deposit-only bank shows a deeply negative NIM
# (interest expense on ~$10M of deposits, almost no earning assets). This posts
# an earning-asset loan book and its interest income straight through the Ledger
# port (`POST /api/v1/ledger/journal`), which the finance snapshot reads.
#
# It only moves GL aggregates — no per-loan accounts or borrowers — so it is a
# demo scaffold, never something an app process or manifest does.
#
# Prereq: bank API on :8081 (a GL core reachable). Run finance accrual first
# (a few days of `/finance/accrue`) so deposit interest expense exists to net
# against; then this; then close the period.
#
#   testing/seed-loan-book.sh                      # $8M book, ~$14k period income
#   LOAN_BOOK=12000000 LOAN_INCOME=21000 testing/seed-loan-book.sh
set -euo pipefail
API="${API_BASE_URL:-http://localhost:8081}"
LOAN_BOOK="${LOAN_BOOK:-8000000.00}"    # earning-asset loan book (Dr loans / Cr bank)
LOAN_INCOME="${LOAN_INCOME:-14000.00}"  # loan interest income for the period

j() {  # post a balanced journal, print the entry id
  curl -fsS -m 15 -XPOST "$API/api/v1/ledger/journal" \
    -H 'content-type: application/json' -d "$1" \
    | python3 -c 'import sys,json; print("   entry", json.load(sys.stdin).get("id"))'
}

echo "🏦 deploying deposits into a \$${LOAN_BOOK} loan book (Dr loans_receivable / Cr bank)"
j "{\"lines\":[{\"account\":\"loans_receivable\",\"direction\":\"debit\",\"amount\":$LOAN_BOOK},
              {\"account\":\"bank\",\"direction\":\"credit\",\"amount\":$LOAN_BOOK}]}"

echo "💵 recognising \$${LOAN_INCOME} loan interest income for the period (Dr bank / Cr interest_income)"
j "{\"lines\":[{\"account\":\"bank\",\"direction\":\"debit\",\"amount\":$LOAN_INCOME},
              {\"account\":\"interest_income\",\"direction\":\"credit\",\"amount\":$LOAN_INCOME}]}"

echo "✅ loan book seeded — close the period and a CFO review will show a believable NIM/RAROC."

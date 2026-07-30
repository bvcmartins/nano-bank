#!/usr/bin/env bash
# Live cross-backend smoke for the reporting service. Runs the unit tests, takes a
# baseline snapshot, seeds interest/fee/interchange activity through nano-bank,
# takes an "after" snapshot, and asserts the reports (balanced Balance Sheet,
# non-zero Income Statement + segment P&L). Run once per CORE_BACKEND.
#
# Prereq: Kind Postgres up; a core up; nano-bank on :8081 against it.
#   CORE_BACKEND=modern bash finance/verify-reports.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=finance/.venv/bin/python
export DB_HOST="${DB_HOST:-::1}"
export NANO_BANK_API="${NANO_BANK_API:-http://localhost:8081}"

# Unique consecutive periods so the snapshots don't collide across runs.
NOW=$(date +%s)
CUR_Y=$((3000 + NOW % 900))
PRIOR="${CUR_Y}-05"
CUR="${CUR_Y}-06"

echo "==> unit tests"
$PY -m pytest finance/tests -q

echo "==> baseline snapshot ($PRIOR)"
$PY -m finance.smoke baseline "$PRIOR"

echo "==> seed interest/fee/interchange activity"
bash testing/verify-nim-engine.sh >/dev/null

echo "==> after snapshot + report assertions ($CUR vs $PRIOR)"
$PY -m finance.smoke report "$CUR" "$PRIOR"

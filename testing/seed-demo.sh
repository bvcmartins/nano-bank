#!/usr/bin/env bash
# DEMO / TEST ONLY. Seed a bounded, *terminating* burst of rail activity so an
# operational review (the COO agent, the viewer, or an e2e test) has non-zero
# numbers to look at. This deliberately lives only in the test harness — no app
# process or k8s manifest ever seeds; production data comes from real traffic.
#
# It reuses the same generator + rail simulators the live harness uses, but runs
# them once with a bounded MAX_CYCLES (0 = forever is the harness default) and
# INTERVAL_SECONDS=0, so each step finishes deterministically instead of looping.
#
# Prereqs (start these first):
#   - bank API on :8081                     (CORE_BACKEND set as desired)
#   - Postgres port-forward on ::1:5432     (the rail simulators read the DB directly)
#
# Usage:
#   testing/seed-demo.sh                     # default small seed
#   CUSTOMERS=20 VISA_CYCLES=100 testing/seed-demo.sh
#   AFT_CYCLES=15 LYNX_CYCLES=10 testing/seed-demo.sh   # also seed AFT + Lynx
set -euo pipefail
cd "$(dirname "$0")"

API_BASE_URL="${API_BASE_URL:-http://localhost:8081}"
DB_HOST="${DB_HOST:-::1}"          # kubectl port-forward binds the IPv6 loopback
DB_PORT="${DB_PORT:-5432}"
# Must match the API's security.service_client_secret (config/default.toml).
SERVICE_CLIENT_SECRET="${SERVICE_CLIENT_SECRET:-nano-bank-visa-network-secret-change-me}"

# How much to seed — a realistic-ish spread by default (all overridable).
CUSTOMERS="${CUSTOMERS:-25}"        # customers (each opens a credit-card account)
VISA_CYCLES="${VISA_CYCLES:-200}"   # card purchases (auth -> capture -> settle)
INTERAC_CYCLES="${INTERAC_CYCLES:-80}"
# Fraction of generated customers who also register an Interac autodeposit
# handle. Without this, INTERAC_CYCLES' inbound path has no handle to land on
# and silently no-ops ("waiting for autodeposit registrations") no matter how
# high INTERAC_CYCLES is set.
INTERAC_HANDLE_PROB="${INTERAC_HANDLE_PROB:-0.6}"
AFT_CYCLES="${AFT_CYCLES:-0}"       # off by default (float/txn/cards already move)
LYNX_CYCLES="${LYNX_CYCLES:-0}"

# Per-rail amount ranges — realistic magnitudes (retail cards, Interac e-Transfers,
# batch AFT, high-value Lynx wires). Each simulator reads MIN_AMOUNT/MAX_AMOUNT.
VISA_MIN="${VISA_MIN:-15}";        VISA_MAX="${VISA_MAX:-2500}"
INTERAC_MIN="${INTERAC_MIN:-50}";  INTERAC_MAX="${INTERAC_MAX:-6000}"
AFT_MIN="${AFT_MIN:-750}";         AFT_MAX="${AFT_MAX:-25000}"
LYNX_MIN="${LYNX_MIN:-25000}";     LYNX_MAX="${LYNX_MAX:-2000000}"

VENV="${SEED_VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "🐍 creating seed venv ($VENV) via uv …"
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" -r requirements-seed.txt >/dev/null
fi
PY="$VENV/bin/python"

run() {  # label ; then the command
  local label="$1"; shift
  echo "== $label =="
  "$@"
}

run "👥 $CUSTOMERS customers (+ credit-card accounts)" \
  env API_BASE_URL="$API_BASE_URL" COUNT="$CUSTOMERS" INTERVAL_SECONDS=0 \
      CREDIT_CARD_PROB=1.0 SAVINGS_PROB=0.5 INTERAC_HANDLE_PROB="$INTERAC_HANDLE_PROB" \
      "$PY" generator/generate_customers.py

run "💳 $VISA_CYCLES card purchases (auth→capture→settle every cycle)" \
  env API_BASE_URL="$API_BASE_URL" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" \
      SERVICE_CLIENT_SECRET="$SERVICE_CLIENT_SECRET" \
      MAX_CYCLES="$VISA_CYCLES" INTERVAL_SECONDS=0 SETTLE_INTERVAL_SECONDS=0 \
      MIN_AMOUNT="$VISA_MIN" MAX_AMOUNT="$VISA_MAX" \
      "$PY" visa/visa_simulator.py

if [ "${INTERAC_CYCLES}" -gt 0 ]; then
  run "📨 Interac activity ($INTERAC_CYCLES cycles, inbound-heavy)" \
    env API_BASE_URL="$API_BASE_URL" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" \
        SERVICE_CLIENT_SECRET="$SERVICE_CLIENT_SECRET" \
        MAX_CYCLES="$INTERAC_CYCLES" INTERVAL_SECONDS=0 INBOUND_PROB=1.0 \
        MIN_AMOUNT="$INTERAC_MIN" MAX_AMOUNT="$INTERAC_MAX" \
        "$PY" interac/interac_simulator.py
fi

if [ "${AFT_CYCLES}" -gt 0 ]; then
  run "🏦 AFT activity ($AFT_CYCLES cycles)" \
    env API_BASE_URL="$API_BASE_URL" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" \
        SERVICE_CLIENT_SECRET="$SERVICE_CLIENT_SECRET" \
        MAX_CYCLES="$AFT_CYCLES" INTERVAL_SECONDS=0 INBOUND_PROB=1.0 \
        MIN_AMOUNT="$AFT_MIN" MAX_AMOUNT="$AFT_MAX" \
        "$PY" aft/aft_simulator.py
fi

if [ "${LYNX_CYCLES}" -gt 0 ]; then
  run "🌐 Lynx activity ($LYNX_CYCLES cycles)" \
    env API_BASE_URL="$API_BASE_URL" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" \
        SERVICE_CLIENT_SECRET="$SERVICE_CLIENT_SECRET" \
        MAX_CYCLES="$LYNX_CYCLES" INTERVAL_SECONDS=0 INBOUND_PROB=1.0 \
        MIN_AMOUNT="$LYNX_MIN" MAX_AMOUNT="$LYNX_MAX" \
        "$PY" lynx/lynx_simulator.py
fi

echo "✅ seed complete — a COO operational review should now show non-zero numbers."

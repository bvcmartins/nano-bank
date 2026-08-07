#!/usr/bin/env bash
# One-command CFO demo: bring the in-cluster stack up, seed the bank + close a
# couple of period snapshots, and run the narrated /ask arc (demos/06-cfo/drive.py).
#
# Runtime is the kind clusters (scripts/deploy-all.sh + cfo/k8s/deploy.sh).
# Seeding is DEMO/TEST-ONLY — bank activity via testing/seed-demo.sh, and the
# period-end snapshots via the CFO's own close_period tool over /ask.
#
#   demos/06-cfo/run-demo.sh                 # up (if needed) -> seed -> drive
#   demos/06-cfo/run-demo.sh --no-up         # assume the stack is already deployed
#   demos/06-cfo/run-demo.sh --no-seed       # leave the data/periods as-is
#   demos/06-cfo/run-demo.sh --beats 1,5     # only these beats
#   demos/06-cfo/run-demo.sh --down          # tear down the port-forwards and exit
#
# Prereqs: docker + kind + kubectl + uv, and (for bring-up) the sibling
# nano-bank-modern-core repo checked out beside this one.
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank
NS=nano-bank

DO_UP=1 DO_SEED=1 BEATS_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-up)   DO_UP=0 ;;
    --no-seed) DO_SEED=0 ;;
    --beats)   BEATS_ARG="--beats $2"; shift ;;
    --down)    DO_UP=0; DO_SEED=0; ONLY_DOWN=1 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

PF_PIDS=()
cleanup() { for pid in "${PF_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT

pf() {  # svc localport [--address ::1]
  local svc="$1" port="$2"; shift 2
  kubectl --context "$CTX" -n "$NS" port-forward "$@" "svc/$svc" "$port:$port" \
    >"/tmp/cfo-demo-pf-$svc.log" 2>&1 &
  PF_PIDS+=($!)
}

wait_http() {  # url label
  echo "⏳ waiting for $2 ($1) ..."
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "❌ $2 never came up at $1"; return 1
}

if [ "${ONLY_DOWN:-0}" = "1" ]; then
  echo "🧹 tearing down any CFO-demo port-forwards ..."
  pkill -f "port-forward.*svc/(bank-api|cfo|postgres-service)" 2>/dev/null || true
  trap - EXIT
  exit 0
fi

if [ "$DO_UP" = "1" ]; then
  echo "🚀 bringing up the stack (modern core + bank + agent, then the CFO) ..."
  SKIP_UI=1 ./scripts/deploy-all.sh
  ./cfo/k8s/deploy.sh
fi

echo "🔌 port-forwards: bank-api:8081, cfo:8089, postgres[::1]:5432 ..."
pf bank-api 8081
pf cfo      8089
pf postgres-service 5432 --address ::1
sleep 3
wait_http http://localhost:8081/health "bank-api"
wait_http http://localhost:8089/livez  "cfo"

if [ "$DO_SEED" = "1" ]; then
  echo "🌱 seeding bank activity (bounded, terminating — demo/test-only) ..."
  CUSTOMERS=25 VISA_CYCLES=200 INTERAC_CYCLES=80 AFT_CYCLES=30 LYNX_CYCLES=15 \
    testing/seed-demo.sh

  # Accrue interest month-to-date (deposit interest expense) and seed a loan book
  # (earning assets + loan income) so the CFO's NIM/RAROC are believable — a
  # deposit-only bank has almost no earning assets. Demo/test-only.
  echo "📈 accruing interest month-to-date + seeding a loan book ..."
  SVC="${SERVICE_CLIENT_SECRET:-nano-bank-visa-network-secret-change-me}"
  TOK=$(curl -s -m5 -XPOST http://localhost:8081/api/v1/auth/service-token \
    -H 'content-type: application/json' -d "{\"client_secret\":\"$SVC\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
  YM=$(date +%Y-%m)
  for i in $(seq 1 "$(date +%d)"); do
    D=$(printf "%02d" "$i")
    curl -s -m 20 -XPOST http://localhost:8081/api/v1/finance/accrue \
      -H "authorization: Bearer $TOK" -H 'content-type: application/json' \
      -d "{\"as_of\":\"$YM-$D\"}" >/dev/null || true
  done
  testing/seed-loan-book.sh

  # Close the prior + current month so reviews (and period-over-period RAROC)
  # have snapshots. The CFO's close_period captures the GL trial balance; the
  # first call is slow (the CFO downloads its memory embedder on cold start).
  CUR="$YM"; PREV=$(date -d 'last month' +%Y-%m)
  for P in "$PREV" "$CUR"; do
    echo "📸 closing period $P via the CFO ..."
    curl -s -m 500 -XPOST http://localhost:8089/ask -H 'content-type: application/json' \
      -d "{\"message\":\"Close period $P. Just confirm it's captured.\"}" >/dev/null || true
  done
fi

# Drive the narrated arc — the driver only speaks HTTP, so a tiny httpx venv.
VENV="demos/06-cfo/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "🐍 creating demo venv ($VENV) via uv ..."
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" httpx >/dev/null
fi

echo "🎬 running the narrated CFO demo ..."
CFO_API_URL=http://localhost:8089 PYTHONPATH="$PWD" \
  "$VENV/bin/python" demos/06-cfo/drive.py $BEATS_ARG

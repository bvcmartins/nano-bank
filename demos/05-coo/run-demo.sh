#!/usr/bin/env bash
# One-command COO demo: bring the in-cluster stack up, seed a bounded burst of
# demo activity, and run the narrated /ask arc (demos/05-coo/drive.py).
#
# Runtime is the kind clusters (see scripts/deploy-all.sh + coo/k8s/deploy.sh).
# Seeding is DEMO/TEST-ONLY — it runs here from the host against a port-forwarded
# bank, never from an app process or a k8s manifest.
#
#   demos/05-coo/run-demo.sh                 # up (if needed) -> seed -> drive
#   demos/05-coo/run-demo.sh --no-up         # assume the stack is already deployed
#   demos/05-coo/run-demo.sh --no-seed       # don't add demo activity (leave data as-is)
#   demos/05-coo/run-demo.sh --beats 1,5     # only these beats
#   demos/05-coo/run-demo.sh --down          # just tear down the port-forwards and exit
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
cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

pf() {  # svc localport [--address ::1]
  local svc="$1" port="$2"; shift 2
  kubectl --context "$CTX" -n "$NS" port-forward "$@" "svc/$svc" "$port:$port" \
    >"/tmp/coo-demo-pf-$svc.log" 2>&1 &
  PF_PIDS+=($!)
}

wait_http() {  # url label
  echo "⏳ waiting for $2 ($1) ..."
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "❌ $2 never came up at $1"; return 1
}

if [ "${ONLY_DOWN:-0}" = "1" ]; then
  echo "🧹 tearing down any COO-demo port-forwards ..."
  pkill -f "port-forward.*svc/(bank-api|coo|postgres-service)" 2>/dev/null || true
  trap - EXIT
  exit 0
fi

if [ "$DO_UP" = "1" ]; then
  echo "🚀 bringing up the stack (modern core + bank + agent, then the COO) ..."
  # The COO demo never touches the web UI (:3000); skip it so an unrelated UI
  # build break can't abort the bank+agent bring-up.
  SKIP_UI=1 ./scripts/deploy-all.sh
  ./coo/k8s/deploy.sh
fi

echo "🔌 port-forwards: bank-api:8081, coo:8093, postgres[::1]:5432 ..."
pf bank-api 8081
pf coo      8093
pf postgres-service 5432 --address ::1
sleep 3
wait_http http://localhost:8081/health "bank-api"
wait_http http://localhost:8093/livez  "coo"

if [ "$DO_SEED" = "1" ]; then
  echo "🌱 seeding demo activity (bounded, terminating — demo/test-only) ..."
  # Realistic-ish volume across every rail (rail-appropriate amounts live in
  # seed-demo.sh). Override any of these on the command line for more.
  CUSTOMERS=25 VISA_CYCLES=200 INTERAC_CYCLES=80 AFT_CYCLES=30 LYNX_CYCLES=15 \
    testing/seed-demo.sh
  # Leave one open outbound AFT batch un-cut so the lever beat has a real action
  # to take (seed-demo's AFT simulator plays the cutoff itself, so we top up a
  # fresh batch AFTER it and let the COO cut this one).
  echo "🌱 leaving one open AFT batch for the COO to cut ..."
  API_URL=http://localhost:8081 python3 demos/05-coo/seed_open_aft.py
fi

# Drive the narrated arc. The driver only speaks HTTP to the COO, so it needs
# just httpx — a tiny venv, not the COO's full (torch/fastembed) requirements.
VENV="demos/05-coo/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "🐍 creating demo venv ($VENV) via uv ..."
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" httpx >/dev/null
fi

echo "🎬 running the narrated COO demo ..."
COO_API_URL=http://localhost:8093 PYTHONPATH="$PWD" \
  "$VENV/bin/python" demos/05-coo/drive.py $BEATS_ARG

# Show the audit trail the lever beat just wrote to — the COO's action is
# recorded, hash-chained and immutable, in a ledger the agent cannot touch.
echo
echo "🔎 inspecting the tamper-evident agent-action ledger ..."
CTX="$CTX" NS="$NS" demos/05-coo/inspect-ledger.sh

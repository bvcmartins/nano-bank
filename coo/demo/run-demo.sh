#!/usr/bin/env bash
# One-command COO demo: bring the in-cluster stack up, seed a bounded burst of
# demo activity, and run the narrated /ask arc (coo/demo/drive.py).
#
# Runtime is the kind clusters (see scripts/deploy-all.sh + coo/k8s/deploy.sh).
# Seeding is DEMO/TEST-ONLY — it runs here from the host against a port-forwarded
# bank, never from an app process or a k8s manifest.
#
#   coo/demo/run-demo.sh                 # up (if needed) -> seed -> drive
#   coo/demo/run-demo.sh --no-up         # assume the stack is already deployed
#   coo/demo/run-demo.sh --no-seed       # don't add demo activity (leave data as-is)
#   coo/demo/run-demo.sh --beats 1,5     # only these beats
#   coo/demo/run-demo.sh --down          # just tear down the port-forwards and exit
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
  ./scripts/deploy-all.sh
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
  # A little of every rail so 'rail activity by status' has something to say.
  INTERAC_CYCLES=20 AFT_CYCLES=8 LYNX_CYCLES=6 VISA_CYCLES=40 CUSTOMERS=8 \
    testing/seed-demo.sh
fi

# Drive the narrated arc from the coo venv (httpx).
VENV="coo/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "🐍 creating coo venv ($VENV) via uv ..."
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" -r coo/requirements.txt >/dev/null
fi

echo "🎬 running the narrated COO demo ..."
COO_API_URL=http://localhost:8093 PYTHONPATH="$PWD" \
  "$VENV/bin/python" coo/demo/drive.py $BEATS_ARG

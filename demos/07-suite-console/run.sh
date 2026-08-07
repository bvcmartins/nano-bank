#!/usr/bin/env bash
# nano-bank C-suite operations console — one command: port-forward the bank + both
# agents, seed one open AFT batch (so the COO's lever has something to cut), and
# launch the single-pane Streamlit console.
#
# Assumes the stack is already deployed in the kind cluster (scripts/deploy-all.sh
# + coo/k8s/deploy.sh + cfo/k8s/deploy.sh). It does NOT bring the cluster up.
#
#   demos/07-suite-console/run.sh              # forwards + seed + console
#   demos/07-suite-console/run.sh --no-seed    # skip seeding the AFT batch
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank
NS=nano-bank
CONSOLE_PORT="${CONSOLE_PORT:-8508}"
DO_SEED=1
[ "${1:-}" = "--no-seed" ] && DO_SEED=0

PF_PIDS=()
cleanup() { for pid in "${PF_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT

pf() {  # svc localport
  kubectl --context "$CTX" -n "$NS" port-forward "svc/$1" "$2:$2" \
    >"/tmp/suite-console-pf-$1.log" 2>&1 &
  PF_PIDS+=($!)
}

wait_http() {  # url label
  echo "⏳ waiting for $2 ($1) ..."
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "❌ $2 never came up at $1"; return 1
}

echo "🔌 port-forwards: bank-api:8081, coo:8093, cfo:8089 ..."
pf bank-api 8081
pf coo      8093
pf cfo      8089
sleep 3
wait_http http://localhost:8081/health "bank-api"
wait_http http://localhost:8093/health "coo"
wait_http http://localhost:8089/health "cfo" || echo "  (CFO optional — the console still runs COO)"

if [ "$DO_SEED" = "1" ]; then
  echo "🌱 seeding one open outbound AFT batch for the COO's lever ..."
  API_URL=http://localhost:8081 python3 demos/05-coo/seed_open_aft.py || true
fi

# Tiny venv — the console only speaks HTTP + shells kubectl for the ledger, so it
# needs just streamlit + httpx (not the agents' torch/fastembed stack).
VENV="demos/07-suite-console/.venv"
if [ ! -x "$VENV/bin/streamlit" ]; then
  echo "🐍 creating console venv ($VENV) via uv ..."
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" streamlit httpx >/dev/null
fi

echo "🎬 launching the C-suite console on http://localhost:$CONSOLE_PORT ..."
BANK_API_URL=http://localhost:8081 \
COO_API_URL=http://localhost:8093 \
CFO_API_URL=http://localhost:8089 \
PYTHONPATH="$PWD" \
  "$VENV/bin/streamlit" run demos/07-suite-console/app.py \
    --server.port "$CONSOLE_PORT" --server.address 0.0.0.0 \
    --browser.gatherUsageStats false

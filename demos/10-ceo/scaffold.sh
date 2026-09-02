#!/usr/bin/env bash
# Full scaffolding for the CEO demo — both the narrated meeting/debate driven
# directly (run-demo.sh) and the boardroom_server.py-captured presentation
# console, which shells out to debate.py/drive.py directly and does no
# seeding of its own. Deploys the five officer seats, then seeds a bank that
# is financially healthy AND has real customer-voice + Interac rail activity
# to reason about — without the latter, the CXO/COO see a near-empty bank and
# the debate's own scripted premise ("recurring e-Transfers is the top
# customer feature request") is ungrounded in what their tools can see.
#
# Unlike run-demo.sh, this does NOT tear its port-forwards down on exit:
# boardroom_server.py's /api/capture needs cfo/coo/cto/cxo/ceo reachable on
# localhost for as long as the console is in use. Kill them yourself when
# done:  kill $(cat /tmp/ceo-scaffold-pf.pids)
#
# Usage:
#   demos/10-ceo/scaffold.sh              # deploy + full wipe + reseed + rail activity
#   demos/10-ceo/scaffold.sh --no-deploy  # officers already up, just reseed
#   demos/10-ceo/scaffold.sh --no-wipe    # skip testing/cleanup.sh (seed on top of current data)
#
# Prereqs: docker + kind + kubectl + uv, the kind-nano-bank (+ kind-modern-core
# for the CTO's cross-cluster reads) clusters up, nano-agent-secrets minted.
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank
NS=nano-bank

DO_DEPLOY=1 DO_WIPE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-deploy) DO_DEPLOY=0 ;;
    --no-wipe)   DO_WIPE=0 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

if [ "$DO_DEPLOY" = 1 ]; then
  echo "🚀 deploying the five officer seats ..."
  # The CTO reads both clusters through this; missing it fails cto/k8s/deploy.sh.
  kubectl --context "$CTX" -n "$NS" get secret nano-platform-kubeconfig >/dev/null 2>&1 || \
    bash platform_mcp/k8s/make-kubeconfig.sh
  for svc in cfo coo cxo; do
    bash "$svc/k8s/deploy.sh" &
  done
  wait
  bash cto/k8s/deploy.sh      # needs nano-platform-kubeconfig above; not safe to parallelize with it
  bash ceo/k8s/deploy.sh      # consults all four — deploy last
fi

echo "🔌 port-forwarding bank-api, postgres, and the officer seats ..."
PIDS_FILE=/tmp/ceo-scaffold-pf.pids
: > "$PIDS_FILE"
pf() {  # svc localport [--address ::1]
  local svc="$1" port="$2"; shift 2
  kubectl --context "$CTX" -n "$NS" port-forward "$@" "svc/$svc" "$port:$port" \
    >"/tmp/ceo-scaffold-pf-$svc.log" 2>&1 &
  echo "$!" >> "$PIDS_FILE"
}
pf bank-api 8081
pf postgres-service 5432 --address ::1
pf ceo 8099
pf cfo 8089
pf coo 8093
pf cto 8095
pf cxo 8098
sleep 4

if [ "$DO_WIPE" = 1 ]; then
  echo "🧹 full bank wipe (testing/cleanup.sh) ..."
  bash testing/cleanup.sh
fi

echo "💰 seeding the calibrated balance sheet (cfo/demo/seed-demo-bank.sh) ..."
if [ ! -x finance/.venv/bin/python ]; then
  python3 -m venv finance/.venv
  finance/.venv/bin/pip install -q -r finance/requirements.txt
fi
bash cfo/demo/seed-demo-bank.sh

echo "🗣  applying the cx schema + seeding customer-voice data ..."
kubectl --context "$CTX" -n "$NS" exec -i deploy/postgres -- \
  psql -U nanobank_user -d nano_bank_db < src/core/tables/10_cx.sql >/dev/null 2>&1 || true
kubectl --context "$CTX" -n "$NS" exec -i deploy/postgres -- \
  psql -U nanobank_user -d nano_bank_db < src/core/tables/11_cx_surveys.sql >/dev/null 2>&1 || true
if [ ! -x cx/.venv/bin/python ]; then
  python3 -m venv cx/.venv
  cx/.venv/bin/pip install -q -r cx/requirements.txt
fi
DB_HOST=::1 cx/.venv/bin/python -m cx.seed_cx_issues
DB_HOST=::1 cx/.venv/bin/python -m cx.seed_surveys

echo "📨 seeding real customer + card + Interac rail activity (testing/seed-demo.sh) ..."
( cd testing && CUSTOMERS=25 VISA_CYCLES=100 INTERAC_CYCLES=80 bash seed-demo.sh )

echo "🪪 seeding KYC completion (cx.seed_kyc) ..."
DB_HOST=::1 cx/.venv/bin/python -m cx.seed_kyc

cat <<MSG

✅ scaffolding complete. Officer port-forwards are running (pids in $PIDS_FILE).
   Kill them when done:  kill \$(cat $PIDS_FILE)
   Now capture the debate:
     curl -XPOST http://localhost:8520/api/capture -d '{"session":"debate"}'
MSG

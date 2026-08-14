#!/usr/bin/env bash
# One-command CTO demo: bring the in-cluster stack up, stage a bad rollout on an
# allow-listed app (cfo), and run the narrated /ask arc (demos/08-cto/drive.py).
# The CTO's own rollback (beat 7) recovers cfo; an EXIT trap restores it if the
# run is aborted mid-arc.
#
# Runtime is the kind clusters (see scripts/deploy-all.sh + cto/k8s/deploy.sh).
# Staging the incident is DEMO-ONLY — it runs here from the host against a
# port-forwarded cluster, never from an app process or a k8s manifest.
#
#   demos/08-cto/run-demo.sh                 # up (if needed) -> break -> drive -> inspect
#   demos/08-cto/run-demo.sh --no-up         # assume the stack is already deployed
#   demos/08-cto/run-demo.sh --no-break      # drive against the estate as-is
#   demos/08-cto/run-demo.sh --beats 6,7     # only these beats
#   demos/08-cto/run-demo.sh --down          # tear down port-forwards, restore cfo, exit
#
# Prereqs: docker + kind + kubectl + uv, and (for bring-up) the sibling
# nano-bank-modern-core repo checked out beside this one.
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank
NS=nano-bank
VICTIM=cfo                          # allow-listed, stateless app we break + recover

DO_UP=1 DO_BREAK=1 BEATS_ARG="" EMIT_ARG="" ONLY_DOWN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-up)    DO_UP=0 ;;
    --no-break) DO_BREAK=0 ;;
    --beats)    BEATS_ARG="--beats $2"; shift ;;
    --emit-jsonl) EMIT_ARG="--emit-jsonl $2"; shift ;;
    --down)     DO_UP=0; DO_BREAK=0; ONLY_DOWN=1 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

PF_PIDS=()
restore_victim() {
  # Idempotent: remove the bad command patch if it is still there and reset the
  # progress deadline. On a clean run beat 7's rollback already recovered cfo
  # (removing the command); this is the abort safety net.
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=json \
    -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' \
    >/dev/null 2>&1 || true
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=merge \
    -p='{"spec":{"progressDeadlineSeconds":600}}' >/dev/null 2>&1 || true
}
cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
}
trap 'cleanup' EXIT

pf() {  # svc localport [--address ::1]
  local svc="$1" port="$2"; shift 2
  kubectl --context "$CTX" -n "$NS" port-forward "$@" "svc/$svc" "$port:$port" \
    >"/tmp/cto-demo-pf-$svc.log" 2>&1 &
  PF_PIDS+=($!)
}

wait_http() {  # url label
  echo "⏳ waiting for $2 ($1) ..."
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "❌ $2 never came up at $1"; return 1
}

if [ "$ONLY_DOWN" = "1" ]; then
  echo "🧹 tearing down CTO-demo port-forwards + restoring $VICTIM ..."
  pkill -f "port-forward.*svc/(bank-api|cto|postgres-service)" 2>/dev/null || true
  restore_victim
  # Close any PRs the delegation beats opened and reset the sandbox to baseline.
  demos/08-cto/reseed-sandbox.sh || true
  trap - EXIT
  exit 0
fi

if [ "$DO_UP" = "1" ]; then
  echo "🚀 bringing up the stack (modern core + bank + agent, then the CTO) ..."
  SKIP_UI=1 ./scripts/deploy-all.sh
  ./cto/k8s/deploy.sh
fi

echo "🔌 port-forwards: bank-api:8081, cto:8095, postgres[::1]:5432 ..."
pf bank-api 8081
pf cto      8095
pf postgres-service 5432 --address ::1
sleep 3
wait_http http://localhost:8081/health "bank-api"
wait_http http://localhost:8095/livez  "cto"

if [ "$DO_BREAK" = "1" ]; then
  echo "💥 staging a bad rollout on $VICTIM so the CTO has a real incident ..."
  # 1) Start from a known-good deployment: drop any stale bad command and restore
  #    the normal progress deadline, then stamp a FRESH good revision (a unique
  #    pod-template annotation). That guarantees the immediate prior revision the
  #    rollback lever targets (second-highest) is this known-good one — robust
  #    even if earlier runs left an alternating good/bad revision history.
  restore_victim
  kubectl --context "$CTX" -n "$NS" rollout status deploy/"$VICTIM" --timeout=120s || true
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=merge \
    -p='{"spec":{"template":{"metadata":{"annotations":{"demo.nano-bank/staged-good":"'"$(date +%s)"'"}}}}}'
  kubectl --context "$CTX" -n "$NS" rollout status deploy/"$VICTIM" --timeout=120s || true
  # 2) Break it: shorten the progress deadline so the bad rollout genuinely STALLS
  #    in seconds (a real ProgressDeadlineExceeded) instead of the 10-minute
  #    default — that stall is exactly the precondition the rollback lever
  #    re-verifies live. progressDeadlineSeconds is a spec field (not part of the
  #    pod template), so only the /bin/false patch creates the new (bad) revision.
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=merge \
    -p='{"spec":{"progressDeadlineSeconds":25}}'
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["/bin/false"]}]'
  # Ensure the abort safety net is armed even though beat 7 should recover it.
  trap 'restore_victim; cleanup' EXIT
  echo "⏳ waiting for the rollout to stall (ProgressDeadlineExceeded) ..."
  for _ in $(seq 1 40); do
    reason=$(kubectl --context "$CTX" -n "$NS" get deploy/"$VICTIM" \
      -o jsonpath='{.status.conditions[?(@.type=="Progressing")].reason}' 2>/dev/null || true)
    if [ "$reason" = "ProgressDeadlineExceeded" ]; then echo "   stalled ✓"; break; fi
    sleep 3
  done
fi

# Reseed the delegation sandbox so beats 8-9 open PRs against a clean baseline
# (close stale cto/* PRs + branches). Best-effort: a skip if the sandbox/gh isn't
# provisioned, so a levers-only run (--beats 6,7) is unaffected.
if [ "$DO_BREAK" = "1" ]; then
  demos/08-cto/reseed-sandbox.sh || echo "⚠ sandbox reseed skipped (gh/sandbox not provisioned)"
fi

# Drive the narrated arc. The driver only speaks HTTP to the CTO, so it needs
# just httpx — a tiny venv, not the CTO's full requirements.
VENV="demos/08-cto/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "🐍 creating demo venv ($VENV) via uv ..."
  uv venv "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" httpx >/dev/null
fi

echo "🎬 running the narrated CTO demo ..."
CTO_API_URL=http://localhost:8095 PYTHONPATH="$PWD" \
  "$VENV/bin/python" demos/08-cto/drive.py $BEATS_ARG $EMIT_ARG

# Show the audit trail the lever beats just wrote to — the CTO's actions
# (a restart refusal + a rollback) are recorded, hash-chained and immutable, in
# a ledger the agent cannot touch.
echo
echo "🔎 inspecting the tamper-evident agent-action ledger ..."
CTX="$CTX" NS="$NS" demos/08-cto/inspect-ledger.sh

# Make sure cfo is healthy again after the demo (rollback should have done it).
echo
echo "🩺 final $VICTIM state ..."
# Reset the demo's short progress deadline before the health check so it reflects
# normal operational config (the EXIT trap also does this on an aborted run).
kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=merge \
  -p='{"spec":{"progressDeadlineSeconds":600}}' >/dev/null 2>&1 || true
# Report health by AVAILABILITY, not `rollout status`: the staged incident leaves a
# terminal ProgressDeadlineExceeded condition on the recovered deployment which
# `rollout status` keeps echoing until the next reconcile — a misleading red error
# as the demo's last line even though the rolled-back pods are up. Poll available
# replicas instead so the closing line reflects reality.
ok=0
for _ in $(seq 1 40); do
  avail=$(kubectl --context "$CTX" -n "$NS" get deploy/"$VICTIM" \
    -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
  want=$(kubectl --context "$CTX" -n "$NS" get deploy/"$VICTIM" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)
  if [ "${avail:-0}" = "${want:-1}" ] && [ "${avail:-0}" != "0" ]; then ok=1; break; fi
  sleep 3
done
if [ "$ok" = "1" ]; then
  echo "   ✅ $VICTIM recovered — ${avail}/${want} replicas available"
else
  echo "   ⚠ $VICTIM not fully available yet (${avail:-0}/${want:-1}); check: kubectl -n $NS get deploy/$VICTIM"
fi

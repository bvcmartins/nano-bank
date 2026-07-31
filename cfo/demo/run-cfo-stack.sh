#!/usr/bin/env bash
# Bring up everything needed to chat with the Agent CFO against the live
# in-cluster bank, then print the console URL.
#
#   bash cfo/demo/run-cfo-stack.sh          # start
#   bash cfo/demo/run-cfo-stack.sh --stop   # tear down
#
# Starts (host-side, all local processes):
#   kubectl port-forward svc/bank-api :8081   finance MCP :8088
#   CFO API :8089                             CFO console :8506
#
# Needs: the kind-nano-bank cluster up, Postgres reachable on ::1:5432,
# an OLLAMA_API_KEY (read from agent/.env), and finance/.venv.
set -euo pipefail

cd "$(dirname "$0")/../.."
RUN_DIR="${TMPDIR:-/tmp}/nano-cfo-demo"
PID_FILE="$RUN_DIR/pids"
CTX="${KUBE_CONTEXT:-kind-nano-bank}"
CONSOLE_PORT="${CONSOLE_PORT:-8506}"

stop() {
  [ -f "$PID_FILE" ] && while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PID_FILE"
  rm -f "$PID_FILE"
  echo "CFO demo stack stopped."
}
[ "${1:-}" = "--stop" ] && { stop; exit 0; }

mkdir -p "$RUN_DIR"; stop >/dev/null 2>&1 || true; : > "$PID_FILE"
start() { # <name> <logfile> <cmd...>
  local name=$1 log=$2; shift 2
  nohup "$@" > "$log" 2>&1 &
  echo $! >> "$PID_FILE"
  printf '   started %-14s -> %s\n' "$name" "$log"
}

echo "== preflight"
python3 -c "
import socket,sys
s=socket.socket(socket.AF_INET6); s.settimeout(3)
sys.exit(0 if s.connect_ex(('::1',5432,0,0))==0 else 1)" \
  || { echo "Postgres not reachable on ::1:5432 — run:
  kubectl --context $CTX -n nano-bank port-forward svc/postgres-service 5432:5432"; exit 1; }
grep -q '^OLLAMA_API_KEY=' agent/.env || { echo "OLLAMA_API_KEY missing from agent/.env"; exit 1; }
echo "   postgres ::1:5432 ok, ollama key present"

echo "== starting services"
# The bank API runs from source: the in-cluster bank-api image predates the
# finance specs (no expanded GL chart, no /finance/accrue). Its GL core lives in
# the separate modern-core cluster, published on the host at :8191.
CORE_URL="${MODERN_CORE_URL:-http://localhost:8191}"
curl -fsS -m 3 "$CORE_URL/health" >/dev/null \
  || { echo "modern core not reachable at $CORE_URL — is the modern-core cluster up?"; exit 1; }
echo "   modern core ok at $CORE_URL"
# cwd must be api/ — the layered config loads config/default.toml relatively.
start bank-api "$RUN_DIR/bank-api.log" bash -c \
  "cd api && CORE_BACKEND=modern MODERN_CORE_URL='$CORE_URL' \
     exec ./target/debug/nano-bank-api"
for _ in $(seq 1 30); do
  curl -fsS -m 2 http://localhost:8081/health >/dev/null 2>&1 && break
  sleep 2
done

source finance/.venv/bin/activate
# Take ONLY the key from agent/.env — sourcing it whole would leak the agent's
# CONSOLE_PORT / NANO_BANK_API / DB_* (in-cluster values) into these processes.
export OLLAMA_API_KEY="$(grep -E '^OLLAMA_API_KEY=' agent/.env | cut -d= -f2-)"
start finance-mcp "$RUN_DIR/finance-mcp.log" python -m finance.mcp_server
start cfo-api     "$RUN_DIR/cfo-api.log"     python -m cfo.api_main
start cfo-console "$RUN_DIR/cfo-console.log" \
  streamlit run cfo/console.py --server.port "$CONSOLE_PORT" --server.headless true

echo "== waiting for the CFO to resolve GLM and come up"
for _ in $(seq 1 40); do
  curl -fsS -m 2 http://localhost:8089/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS -m 3 http://localhost:8081/health >/dev/null || { echo "bank API not up"; exit 1; }
curl -fsS -m 3 http://localhost:8089/health >/dev/null || { echo "CFO API not up — see $RUN_DIR/cfo-api.log"; exit 1; }

cat <<EOF

  Agent CFO is up.

    Chat console : http://localhost:$CONSOLE_PORT
    A2A endpoint : curl -XPOST localhost:8089/ask -H 'content-type: application/json' \\
                     -d '{"message":"How healthy are we?"}'

    Seed a demo bank : bash cfo/demo/seed-demo-bank.sh
    Stop everything  : bash cfo/demo/run-cfo-stack.sh --stop
    Logs             : $RUN_DIR
EOF

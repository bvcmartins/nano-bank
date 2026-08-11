#!/usr/bin/env bash
# Inspect the tamper-evident agent-action ledger for the CTO demo: every
# state-changing action an agent took (here: the CTO's restart refusals and
# rollback), hash-chained and immutable. Reads straight from Postgres in the
# kind cluster (no host DB driver) and runs the server-side chain verifier. The
# ledger is out of bounds for the agents themselves — this is an auditor view.
#
#   demos/08-cto/inspect-ledger.sh              # full ledger + chain check
#   demos/08-cto/inspect-ledger.sh --tamper-demo  # prove UPDATE/DELETE are rejected
set -euo pipefail
CTX="${CTX:-kind-nano-bank}"
NS="${NS:-nano-bank}"
PSQL=(psql -U nanobank_user -d nano_bank_db)

pod() { kubectl --context "$CTX" -n "$NS" get pod -l app=postgres \
          -o jsonpath='{.items[0].metadata.name}'; }
PG="$(pod)"
q() { kubectl --context "$CTX" -n "$NS" exec -i "$PG" -- "${PSQL[@]}" "$@"; }

echo "🔗 Agent-action ledger  (cluster=$CTX  ns=$NS  pod=$PG)"
echo

# Each row's prev_hash must equal the prior row's entry_hash (shown truncated so
# the linkage is legible). The detail column surfaces the CTO lever specifics:
# which deployment, and the restart/rollback effect or the refusal reason.
q -P pager=off -c "
SELECT seq,
       to_char(ts,'YYYY-MM-DD HH24:MI:SS') AS ts_utc,
       actor,
       action,
       COALESCE(effect->>'outcome','—')                         AS outcome,
       COALESCE(params->>'deployment','')                       AS deployment,
       COALESCE(effect->'effect'->>'rolled_back_to',
                effect->'effect'->>'restarted_at',
                effect->>'reason',
                '')                                             AS detail,
       left(prev_hash,10)  AS prev_hash,
       left(entry_hash,10) AS entry_hash
FROM agent_action_ledger
ORDER BY seq;"

echo
BROKEN="$(q -At -c "SELECT verify_agent_ledger();")"
if [ -z "$BROKEN" ]; then
  echo "✅ chain INTACT — every prev_hash links to the prior entry_hash"
else
  echo "❌ chain BROKEN at seq $BROKEN — the ledger has been tampered with"
  exit 1
fi

if [ "${1:-}" = "--tamper-demo" ]; then
  echo
  echo "🔒 immutability (the agents cannot rewrite history):"
  printf '   UPDATE → '; q -c \
    "UPDATE agent_action_ledger SET effect='{\"outcome\":\"tampered\"}' WHERE seq=1;" \
    2>&1 | grep -m1 -iE "append-only|ERROR" || true
  printf '   DELETE → '; q -c \
    "DELETE FROM agent_action_ledger WHERE seq=1;" \
    2>&1 | grep -m1 -iE "append-only|ERROR" || true
fi

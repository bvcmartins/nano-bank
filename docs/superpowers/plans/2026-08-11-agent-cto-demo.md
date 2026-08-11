# Agent CTO narrated demo (`demos/08-cto/`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a one-command narrated demo (`demos/08-cto/`) that shows the Agent CTO reviewing the platform across both kind clusters as a grounded analyst and then autonomously recovering a staged bad rollout via `execute_rollback`, with every action landing in the tamper-evident audit ledger.

**Architecture:** Mirror the existing `05-coo`/`06-cfo` demo shape — a `run-demo.sh` orchestrator (the only thing that mutates the cluster: bring up → stage a bad rollout on `cfo` → drive → inspect the ledger → restore), a pure `drive.py` of BEATS over the shared `demos/_driver.py`, a `questions.md`/`README.md`, and a CTO-flavored `inspect-ledger.sh`. One small code change: refine the CTO operator prompt so it selects the *right* lever (transient → restart, bad revision → rollback).

**Tech Stack:** Bash + kubectl (kind), Python 3.12 + httpx (tiny demo venv via `uv`), the Phase A/B CTO stack (FastAPI `:8095`, platform_mcp `:8094`, Postgres `agent_action_ledger`).

## Global Constraints

- Snap-env before any kubectl/docker/kind: `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- The Python package dir is `platform_mcp` (never `platform` — it shadows stdlib). K8s names stay `platform-mcp`.
- Bank DB host from the host is `::1` (not 127.0.0.1); in-cluster psql goes through `kubectl exec` (no host driver).
- Only `run-demo.sh` mutates the cluster, from the **host** against a port-forwarded cluster — never from an app process or a k8s manifest.
- The real MCP tool names are exactly: `estate_health()`, `service_health()`, `execute_rollout_restart(cluster, deployment)`, `execute_rollback(cluster, deployment)`.
- Repo venv for Python tests: `.venv/bin/python` (already present in this worktree).
- Truth constraint: a rollout-restart does NOT fix a bad revision; only rollback does. The demo's genuine recovery uses rollback; restart is shown as a refusal on a healthy target.
- Branch: do this work on `agent-cto-demo`, stacked on `agent-cto-levers`, so PR #68 (levers) stays clean.

---

### Task 1: Refine the CTO operator prompt to select the right lever

The Phase B prompt says "call the lever" generically. Guide selection so a stalled/bad revision drives `execute_rollback` and a transient crashloop drives `execute_rollout_restart`. This is the only code change; it edits the `agent-cto-levers` branch content, so it needs its own test.

**Files:**
- Modify: `csuite/tests/fakes.py` (add a fake `execute_rollback` tool)
- Modify: `cto/tests/test_agent.py` (add the lever-selection test)
- Modify: `cto/agent.py` (refine `CTO_PROMPT`)

**Interfaces:**
- Consumes: `fake_platform_tools()` returning a list of LangChain tools incl. `estate_health`, `service_health`, `execute_rollout_restart(cluster, deployment)`; `FakeChatModel(script)`; `agent_mod.ask(settings, message, *, memory)` returning `{"answer","verification","trace"}`.
- Produces: `fake_platform_tools()` additionally returns `execute_rollback(cluster, deployment)`; `CTO_PROMPT` string that maps a bad/stalled revision → rollback.

- [ ] **Step 1: Add a fake `execute_rollback` tool**

In `csuite/tests/fakes.py`, inside `fake_platform_tools()`, after the `execute_rollout_restart` tool and before `return`:

```python
    @tool
    def execute_rollback(cluster: str, deployment: str) -> dict:
        """Canned rollback lever."""
        return {"outcome": "executed",
                "effect": {"rolled_back_to": 5}}

    return [estate_health, service_health, execute_rollout_restart,
            execute_rollback]
```

(Replace the existing `return [estate_health, service_health, execute_rollout_restart]` line.)

- [ ] **Step 2: Write the failing lever-selection test**

Append to `cto/tests/test_agent.py`:

```python
def test_cto_rolls_back_a_bad_revision(monkeypatch):
    # A stalled rollout on a bad revision must drive execute_rollback (not
    # restart — a restart re-runs the same broken spec).
    model = FakeChatModel([
        {"tool": "estate_health", "args": {}},
        {"tool": "execute_rollback",
         "args": {"cluster": "nano-bank", "deployment": "cfo"}},
        {"text": "Rolled cfo back to the last good revision (executed)."},
    ])
    _patch(monkeypatch, model)
    out = asyncio.run(agent_mod.ask(_settings(),
                                    "cfo is crashlooping on a bad rollout — fix it",
                                    memory=SafeMemory(None)))
    assert any(e.get("name") == "execute_rollback" for e in out["trace"])
    assert "executed" in out["answer"]
```

- [ ] **Step 3: Run it — expect PASS already (scripted model), then confirm the whole file is green**

Run: `cd /home/bmartins/dev/nano-bank/.claude/worktrees/agent-cto-levers && .venv/bin/python -m pytest cto/tests/test_agent.py -q`
Expected: PASS (the scripted model already drives the tool; this test locks the behavior in). If it errors on a missing `execute_rollback` fake, Step 1 was not applied.

- [ ] **Step 4: Refine the prompt for lever selection**

In `cto/agent.py`, replace the operator paragraph sentence:

Old:
```python
    "and an ANALYST for everything else. Your levers are `execute_rollout_restart` "
    "and `execute_rollback` over stateless app deployments in either cluster. When "
    "a deployment you can act on is unhealthy — crashlooping, or ready<desired, or "
    "a rollout stalled on a bad revision — DON'T ASK, ACT: call the lever. The "
```

New:
```python
    "and an ANALYST for everything else. Your levers are `execute_rollout_restart` "
    "and `execute_rollback` over stateless app deployments in either cluster. When "
    "a deployment you can act on is unhealthy, DON'T ASK, ACT — but choose the "
    "RIGHT lever: for a TRANSIENT fault on an otherwise-good revision (wedged pods, "
    "a one-off crashloop, ready<desired) use `execute_rollout_restart`; for a BAD "
    "or STALLED revision with a healthy prior revision use `execute_rollback` (a "
    "restart only re-runs the same broken spec, so it will NOT fix a bad rollout). The "
```

- [ ] **Step 5: Run cto + csuite green**

Run: `.venv/bin/python -m pytest cto csuite -q`
Expected: PASS (54 tests — the prior 53 plus the new one).

- [ ] **Step 6: Commit**

```bash
git add cto/agent.py cto/tests/test_agent.py csuite/tests/fakes.py
git commit -m "feat(cto): steer lever selection — rollback a bad revision, restart a transient fault"
```

---

### Task 2: `demos/08-cto/inspect-ledger.sh`

A CTO-flavored copy of `demos/05-coo/inspect-ledger.sh` — the ledger renderer + chain verifier already show any actor (incl. `cto`); adjust the detail column to surface restart/rollback effects.

**Files:**
- Create: `demos/08-cto/inspect-ledger.sh`

**Interfaces:**
- Consumes: in-cluster `agent_action_ledger` (columns `seq, ts, actor, action, params, effect, prev_hash, entry_hash`), `verify_agent_ledger()` (returns the seq of the first bad row, NULL if intact), the append-only immutability trigger.
- Produces: an operator/auditor view; no outputs consumed by later tasks.

- [ ] **Step 1: Create the script**

```bash
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
```

- [ ] **Step 2: Make executable + syntax-check**

Run:
```bash
chmod +x demos/08-cto/inspect-ledger.sh
bash -n demos/08-cto/inspect-ledger.sh && echo "syntax ok"
```
Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add demos/08-cto/inspect-ledger.sh
git commit -m "feat(demo): CTO agent-action ledger inspector (08-cto)"
```

---

### Task 3: `demos/08-cto/drive.py` — the narrated 8-beat arc

**Files:**
- Create: `demos/08-cto/drive.py`

**Interfaces:**
- Consumes: `demos/_driver.py` `run(beats, *, api_url, agent_label, run_hint)`; each beat is `{"title","shows","message","thread"}`; the driver probes `GET {api_url}/health` and POSTs `/ask`. NOTE the driver requires `/health` to return `{"status":"ok"}`; the CTO's `/health` does live dependency probes and can be slow — Task 4 gates readiness on `/livez` before driving, but the driver itself still calls `/health`, which returns 200 `{"status":"ok"}` once the model backend is reachable.
- Produces: `BEATS` list; run entrypoint using `CTO_API_URL` (default `http://localhost:8095`).

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""Narrated CTO demo — the beats; rendering/streaming lives in demos/_driver.py.

    CTO_API_URL=http://localhost:8095 python demos/08-cto/drive.py
    python demos/08-cto/drive.py --beats 6,7      # guardrail + recovery only

The estate is staged with a bad rollout on cfo BEFORE driving (see
demos/08-cto/run-demo.sh); beat 7's rollback genuinely recovers it, beat 8
verifies. A restart would NOT fix a bad revision, so restart appears only as a
refusal on a healthy target (beat 6).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # demos/
from _driver import run  # noqa: E402

BEATS = [
    {
        "title": "Grounded estate + delivery review (both clusters) + subagent",
        "shows": "a full platform review where every figure is tool-grounded; the "
                 "harness plans, keeps todos, and spawns a subagent to deep-dive the "
                 "one unhealthy service — surfacing the staged cfo bad-rollout",
        "message": "Give me a reliability and delivery review across BOTH clusters — "
                   "deployment health, crashloops, restart counts, rollout status and "
                   "image drift, with the numbers. Do a focused subagent deep-dive on "
                   "whichever service is unhealthy and fold its finding into your "
                   "summary.",
        "thread": "new",
    },
    {
        "title": "Derived figure, still grounded (compute)",
        "shows": "a share the raw tools don't return: the CTO pulls degraded vs total "
                 "deployments and calls the compute tool to make a %",
        "message": "What share of deployments across the estate are degraded right "
                   "now? Give me the percentage.",
        "thread": "new",
    },
    {
        "title": "Memory — record a durable platform note",
        "shows": "the CTO records a durable reliability observation for later reviews",
        "message": "For the record: note the cfo bad-rollout incident you just found "
                   "and the one reliability risk you'd watch. Record it as a durable "
                   "platform note.",
        "thread": "mem-write",
    },
    {
        "title": "Memory — recall it in a fresh thread",
        "shows": "a NEW conversation with no shared state recalls the earlier note "
                 "from durable memory (Qdrant), not from in-thread history",
        "message": "Earlier you recorded a durable platform note about a rollout "
                   "incident and a risk to watch. Recall it and tell me where the "
                   "platform team should focus.",
        "thread": "new",
    },
    {
        "title": "Scope discipline — the books are the CFO's",
        "shows": "asked a P&L question, the CTO defers to the CFO and stays in the "
                 "technical lane (it cannot see the books)",
        "message": "What was our net interest margin and RAROC last month?",
        "thread": "new",
    },
    {
        "title": "Guardrail — a restart is REFUSED on a healthy target",
        "shows": "the CTO won't act without a real fault: asked to restart a healthy "
                 "service, the lever self-verifies live, finds nothing wrong, and "
                 "REFUSES — and the refusal is written to the tamper-evident ledger",
        "message": "coo looks fine but roll it anyway to pick up a rotated secret — "
                   "restart the coo deployment now.",
        "thread": "new",
    },
    {
        "title": "Autonomous recovery — the CTO rolls back a bad revision",
        "shows": "the CTO doesn't just report: it ACTS. Seeing cfo crashlooping on a "
                 "stalled bad rollout with a healthy prior revision, it pulls "
                 "execute_rollback on its own judgement — no human confirmation. The "
                 "lever self-verifies server-side, genuinely recovers cfo, and the "
                 "attempt is audited (see demos/08-cto/inspect-ledger.sh)",
        "message": "cfo is crashlooping on a bad rollout. Fix it — don't ask me "
                   "first. Then tell me exactly what you did and the effect the bank "
                   "returned.",
        "thread": "new",
    },
    {
        "title": "Verify the fix held",
        "shows": "the CTO confirms its own recovery: a fresh estate read shows cfo "
                 "healthy again after the rollback",
        "message": "Re-check cfo now — did the rollback take? Is it healthy again?",
        "thread": "new",
    },
]

if __name__ == "__main__":
    raise SystemExit(run(
        BEATS,
        api_url=os.environ.get("CTO_API_URL", "http://localhost:8095"),
        agent_label="Agent CTO",
        run_hint="demos/08-cto/run-demo.sh",
    ))
```

- [ ] **Step 2: Byte-compile check**

Run: `.venv/bin/python -m py_compile demos/08-cto/drive.py && echo "compile ok"`
Expected: `compile ok`.

- [ ] **Step 3: Commit**

```bash
git add demos/08-cto/drive.py
git commit -m "feat(demo): CTO narrated 8-beat arc (analyst + audited self-recovery)"
```

---

### Task 4: `demos/08-cto/run-demo.sh` — orchestration (up → break → drive → inspect → restore)

**Files:**
- Create: `demos/08-cto/run-demo.sh`

**Interfaces:**
- Consumes: `scripts/deploy-all.sh` (env `SKIP_UI=1`), `cto/k8s/deploy.sh`, `platform_mcp/k8s/make-kubeconfig.sh`, `demos/08-cto/drive.py`, `demos/08-cto/inspect-ledger.sh`; kubectl contexts `kind-nano-bank`; CTO `/livez` on `:8095`, bank `/health` on `:8081`.
- Produces: the one-command demo entry point; no outputs consumed by later tasks.

- [ ] **Step 1: Create the file**

```bash
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

DO_UP=1 DO_BREAK=1 BEATS_ARG="" ONLY_DOWN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-up)    DO_UP=0 ;;
    --no-break) DO_BREAK=0 ;;
    --beats)    BEATS_ARG="--beats $2"; shift ;;
    --down)     DO_UP=0; DO_BREAK=0; ONLY_DOWN=1 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

PF_PIDS=()
restore_victim() {
  # Idempotent: remove the bad command patch if it is still there. On a clean
  # run beat 7's rollback already recovered cfo; this is the abort safety net.
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=json \
    -p='[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' \
    >/dev/null 2>&1 || true
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
  echo "💥 staging a bad rollout on $VICTIM (command → /bin/false) so the CTO has a real incident ..."
  kubectl --context "$CTX" -n "$NS" patch deploy/"$VICTIM" --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["/bin/false"]}]'
  # Let it become observably degraded (a stalled rollout with a healthy prior revision).
  kubectl --context "$CTX" -n "$NS" rollout status deploy/"$VICTIM" --timeout=40s || true
  # Ensure the abort safety net is armed even though beat 7 should recover it.
  trap 'restore_victim; cleanup' EXIT
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
  "$VENV/bin/python" demos/08-cto/drive.py $BEATS_ARG

# Show the audit trail the lever beats just wrote to — the CTO's actions
# (a restart refusal + a rollback) are recorded, hash-chained and immutable, in
# a ledger the agent cannot touch.
echo
echo "🔎 inspecting the tamper-evident agent-action ledger ..."
CTX="$CTX" NS="$NS" demos/08-cto/inspect-ledger.sh

# Make sure cfo is healthy again after the demo (rollback should have done it).
echo
echo "🩺 final $VICTIM state ..."
kubectl --context "$CTX" -n "$NS" rollout status deploy/"$VICTIM" --timeout=120s || true
```

- [ ] **Step 2: Make executable + syntax-check**

Run:
```bash
chmod +x demos/08-cto/run-demo.sh
bash -n demos/08-cto/run-demo.sh && echo "syntax ok"
```
Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add demos/08-cto/run-demo.sh
git commit -m "feat(demo): one-command CTO demo runner (up -> break -> drive -> inspect -> restore)"
```

---

### Task 5: Docs — `questions.md`, `README.md`, and the `demos/README.md` index row

**Files:**
- Create: `demos/08-cto/questions.md`
- Create: `demos/08-cto/README.md`
- Modify: `demos/README.md` (add row 8 to the table; extend the "demos 5–6 are a different shape" note to 5–6, 8)

**Interfaces:**
- Consumes: nothing at runtime — reference docs.
- Produces: the runbook + question sheet; the series index entry.

- [ ] **Step 1: Create `demos/08-cto/questions.md`**

```markdown
# Agent CTO — demo question sheet

The narrated arc (`drive.py`) runs these in order. You can also paste them into
the live console (`cto/console.py`, `:8509`) one at a time.

The estate is staged with a **bad rollout on `cfo`** before the arc runs
(`run-demo.sh` patches its container command to `/bin/false`). Beat 7's rollback
genuinely recovers it; beat 8 verifies. A restart would only re-run the broken
spec, so restart appears as a **refusal** on a healthy target (beat 6).

1. **Estate + delivery review (both clusters) + subagent** — "Give me a
   reliability and delivery review across BOTH clusters … deep-dive whichever
   service is unhealthy." → grounded review that surfaces the cfo incident.
2. **Derived figure (compute)** — "What share of deployments are degraded right
   now?" → degraded/total via the compute tool.
3. **Memory — record** — "Note the cfo bad-rollout incident and the risk you'd
   watch. Record it as a durable platform note."
4. **Memory — recall (fresh thread)** — "Recall the platform note about a rollout
   incident …" → from Qdrant, not in-thread state.
5. **Scope discipline** — "What was our net interest margin and RAROC last
   month?" → defers to the CFO.
6. **Restart REFUSED (guardrail)** — "coo looks fine but restart it to pick up a
   rotated secret." → the lever self-verifies, finds no fault, refuses; audited.
7. **Autonomous recovery — rollback** — "cfo is crashlooping on a bad rollout.
   Fix it — don't ask me first." → the CTO pulls `execute_rollback`, cfo
   recovers; audited as executed.
8. **Verify** — "Re-check cfo — did the rollback take?" → healthy again.

After the arc, `run-demo.sh` runs `inspect-ledger.sh`: the `cto` rows (a
`refused` restart + an `executed` rollback), the chain verifier (INTACT), and
`--tamper-demo` proving UPDATE/DELETE are rejected.
```

- [ ] **Step 2: Create `demos/08-cto/README.md`**

```markdown
# Demo 8 — Agent CTO (analyst + audited self-recovery)

The **Chief Technology Officer** agent over the platform MCP: a grounded analyst
across BOTH kind clusters (reliability + delivery) that also **acts** — it
autonomously **rolls back a bad deployment** and every action lands in the
tamper-evident `agent_action_ledger`. Same `csuite` harness as the COO/CFO demos.

## What it shows

An 8-beat narrated arc (`drive.py`): a grounded estate/delivery review across
both clusters with planning + a subagent deep-dive; a degraded-share figure via
the compute tool; durable memory recorded and recalled in a fresh thread; scope
discipline (the books are the CFO's); a **restart refused** on a healthy target
(the self-verify guardrail); an **autonomous rollback** that genuinely recovers a
staged bad rollout on `cfo`; and a verification beat. Then the audit ledger is
inspected.

## Honesty note

A rollout-restart cycles pods; it does **not** fix a bad revision. So the demo's
genuine recovery uses **rollback** against a staged bad rollout, and **restart**
is shown as a **refusal** on a healthy service. Nothing is faked — both levers
appear on the conditions they actually apply to.

## Run it

```bash
# one command: bring up -> stage the incident -> drive -> inspect the ledger
demos/08-cto/run-demo.sh

# against an already-deployed stack:
demos/08-cto/run-demo.sh --no-up

# fast path — just the guardrail + recovery beats:
demos/08-cto/run-demo.sh --beats 6,7

# tear down port-forwards and restore cfo:
demos/08-cto/run-demo.sh --down
```

Prereqs: docker + kind + kubectl + uv, and (for bring-up) the sibling
`nano-bank-modern-core` repo beside this one. The runner stages the incident from
the host against a port-forwarded cluster (demo-only), and an EXIT trap restores
`cfo` if a run is aborted mid-arc.

## Inspect the audit trail

```bash
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh --tamper-demo
```
```

- [ ] **Step 3: Add the index row to `demos/README.md`**

In the table after the row that starts `| 6 | Agent CFO |`, add:

```markdown
| 8 | Agent CTO | `08-cto/` | The **Chief Technology Officer** agent (platform analyst + operator on the `csuite` harness): an 8-beat arc across BOTH kind clusters — grounded reliability/delivery review with planning + a subagent deep-dive, a degraded-share figure via compute, durable memory, scope discipline (the books are the CFO's), a **restart refused** on a healthy target (the self-verify guardrail), and an **autonomous rollback** that genuinely recovers a staged bad rollout on `cfo` — every action audited in the tamper-evident ledger. One-command runner + live console. See `08-cto/README.md`. |
```

Then update the shape note below the table from:

```markdown
Demos 5–6 are a different shape from 1–4: not a single `app.py` over the bank API
```

to:

```markdown
Demos 5–6 and 8 are a different shape from 1–4: not a single `app.py` over the bank API
```

- [ ] **Step 4: Commit**

```bash
git add demos/08-cto/questions.md demos/08-cto/README.md demos/README.md
git commit -m "docs(demo): CTO demo runbook, question sheet, and series index row"
```

---

### Task 6: Live acceptance run (no new code)

Prove the whole demo works against the real clusters, per superpowers:verification-before-completion (run it, confirm output before claiming success).

**Files:** none (execution only).

**Interfaces:** consumes everything built above.

- [ ] **Step 1: Ensure the stack is up (Phase B images already built/loaded this session)**

Run: `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share && kubectl --context kind-nano-bank -n nano-bank get deploy cfo coo cto platform-mcp bank-api`
Expected: all present and available. If the CTO image predates Task 1's prompt change, rebuild + reload + roll cto: `docker build -f cto/Dockerfile -t nano-cto:dev . && kind load docker-image nano-cto:dev --name nano-bank && kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/cto && kubectl --context kind-nano-bank -n nano-bank rollout status deploy/cto --timeout=240s`.

- [ ] **Step 2: Run the guardrail + recovery fast path first**

Run: `demos/08-cto/run-demo.sh --no-up --beats 6,7`
Expected: beat 6 renders a **refused** restart of `coo`; beat 7 renders an **executed** `execute_rollback` of `cfo`; the ledger inspection shows a `cto | rollout_restart | refused` row and a `cto | rollback | executed` row with chain **INTACT**; final `cfo` rollout status succeeds.

- [ ] **Step 3: Run the full arc**

Run: `demos/08-cto/run-demo.sh --no-up`
Expected: all 8 beats render; beat 1 surfaces the cfo incident and spawns a subagent; beat 2 shows a compute-derived %; beats 3–4 show memory record/recall; beat 5 defers to the CFO; beats 6–7 as above; beat 8 shows cfo healthy again. Ledger INTACT.

- [ ] **Step 4: Confirm cfo fully recovered + tamper demo**

Run:
```bash
kubectl --context kind-nano-bank -n nano-bank get deploy cfo -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'; echo
CTX=kind-nano-bank NS=nano-bank demos/08-cto/inspect-ledger.sh --tamper-demo
```
Expected: `1/1`; UPDATE and DELETE both rejected (append-only); chain INTACT.

- [ ] **Step 5: No commit** — this task changes no files. If the run surfaced any doc/script fix, make it in the relevant task's file and commit there.

---

## Self-Review

**Spec coverage:**
- `demos/08-cto/` layout (run-demo/drive/questions/README/inspect-ledger) → Tasks 2–5.
- Truth constraint (rollback recovers, restart refused) → Task 1 (prompt), Task 3 (beats 6/7), Task 4 (stage bad rollout), READMEs (Task 5).
- No bank-data seeding → Task 4 stages only the incident.
- Orchestration up→break→drive→inspect→restore, host-only mutation, restore trap → Task 4.
- 8-beat arc exactly as specified → Task 3.
- Prompt refinement + its test → Task 1.
- Ledger inspector (any actor, verifier, tamper-demo) → Task 2.
- README index row 8 → Task 5.
- Live acceptance → Task 6.

**Placeholder scan:** every script/doc body is written in full; no TBD/TODO/"similar to". The one intentional runtime detail (rebuild cto if the image predates Task 1) is a concrete conditional command, not a placeholder.

**Type/name consistency:** tool names `estate_health`/`service_health`/`execute_rollout_restart(cluster, deployment)`/`execute_rollback(cluster, deployment)` are identical across `fakes.py`, the test, `drive.py`, and `inspect-ledger.sh`'s detail extraction. Ledger keys used by the inspector match the Phase B write path verified in `platform_mcp/mcp_server.py` + `k8s_writer.py`: `action` is `rollout_restart`/`rollback`; `params->>'deployment'`; executed → `effect->>'outcome'='executed'` with `effect->'effect'->>'restarted_at'` (restart) / `effect->'effect'->>'rolled_back_to'` (rollback); refused → `effect->>'reason'`. Beat/flag names (`--no-up`/`--no-break`/`--beats`/`--down`) match between `run-demo.sh` and both READMEs.

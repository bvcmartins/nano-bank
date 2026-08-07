# C-suite operations console — demo

A **single pane** where you drive an autonomous nano-bank officer and watch its
work *and* the tamper-evident audit trail at the same time.

```
┌───────────────────────────────┬────────────────────────────┐
│  🏭 Agent COO / 📒 Agent CFO   │  🔗 Agent-action ledger     │
│                               │  ✅ chain INTACT (12)       │
│  Q: cut the AFT batch…        │  12  cut_aft_batch  🟢       │
│  💬 Answer: executed, batch…  │  11  cut_aft_batch  🟢       │
│  ✓ tool-grounded              │  10  reject_stale   ⛔       │
│  🔧 rails, execute_cut_aft…   │   1  close_period (cfo)     │
└───────────────────────────────┴────────────────────────────┘
```

- **Left — chat.** Pick the agent (COO or CFO), then click a **preset beat** or
  type your own question. It streams the run live and then shows the answer, the
  grounding **badge**, and the harness trace (plan · todos · tools · subagent ·
  memory) in an expandable run-tree.
- **Right — the live agent-action ledger.** Every state-changing action any agent
  takes, hash-chained and immutable. When the COO pulls a lever, the new row lands
  here and the chain stays **INTACT** — you see the action and its audit trail
  together.

## Run it

The stack must already be deployed in the kind cluster (`scripts/deploy-all.sh` +
`coo/k8s/deploy.sh` + `cfo/k8s/deploy.sh`). Then:

```bash
demos/07-suite-console/run.sh            # forwards + seed an AFT batch + console
demos/07-suite-console/run.sh --no-seed  # skip seeding
```

Open **http://localhost:8508** (or `airig.local:8508` from the LAN). The script
port-forwards `bank-api:8081`, `coo:8093`, `cfo:8089`, seeds one open outbound
AFT batch so the COO's cut-batch lever has a real action to take, and launches the
Streamlit app.

## What to try

1. **COO → beat 8 "Autonomous action".** The COO checks the AFT batch and pulls
   `execute_cut_aft_batch` on its own — watch a new `cut_aft_batch · 🟢 executed`
   row appear on the right, chain still INTACT. Click **🌱 Seed open AFT batch**
   first (sidebar) to line one up; run it again with no batch and you'll see the
   lever **⛔ refused** — also audited.
2. **COO → the read/scope beats.** Grounded reviews, the `compute` tool, scope
   refusals (fraud is out of bounds; the books are the CFO's).
3. **Switch to CFO.** Close a period / ask for NIM / RAROC — its `close_period`
   writes to the *same* ledger (seq 1), proving the audit trail spans every agent.
4. **Memory.** Record a durable note, hit **🧵 New conversation**, then ask the
   agent to recall it — the recall comes from durable storage, not the chat.

## How it's wired

- The console only speaks **HTTP** to the agents' `/ask/stream` endpoints and
  reuses the shared `csuite` streaming/trace renderers — no agent code changes.
- The ledger panel reads Postgres via `kubectl exec deploy/postgres` (the same
  password-free path as `demos/05-coo/inspect-ledger.sh`) — no DB driver, no
  credentials in the demo. It refreshes on every interaction (and via the
  **🔄 Refresh ledger** button).
- Preset beats are imported straight from `demos/05-coo/drive.py` and
  `demos/06-cfo/drive.py`, so this console and the narrated scripts stay in sync.

## Notes

- Each beat is a real model call — multi-step beats take tens of seconds. That's
  the model working, not a hang.
- The ledger is **out of bounds for the agents**; this panel is the operator view.
  `demos/05-coo/inspect-ledger.sh --tamper-demo` proves UPDATE/DELETE are rejected.

# demos/

Interactive **demo views** for nano-bank — guided Streamlit apps that drive the
real REST API to show a flow end to end. Distinct from `testing/` (the automated
harness + payment-network simulators): these are for *showing* the bank, one demo
per subdirectory.

| # | Demo | Dir | What it shows |
|---|------|-----|---------------|
| 1 | Onboarding | `01-onboarding/` | Create a customer → open accounts → post deposit/withdrawal/transfer, over the consumer API (identity from the customer JWT). |
| 2 | Activity simulator | `02-simulator/` | Auto-generate customers, accounts (all types), and transactions of every type **including deliberate failures**; register Interac payees + send over the real rail; a final **timestamped event-log** tab streams every API call (green/red). |
| 3 | Manager chat | `03-manager-chat/` | The personal manager as a **left-right conversation** (you ← → manager) with the run trace inline; five pre-filled boxes incl. **account balance** and **savings-account advice**. Branch API (`:8086`). |
| 4 | External mandated agent | `04-external-agent/` | An **autonomous LLM agent** operating a customer's bank **only through the agentic branch** (`/agent-gateway/*`), under a customer-granted **mandate** (scoped, capped, revocable): a high-level instruction → plan → mandate-gated acts (bill payment to Epcor) + A2A to the manager, with a Revoke button. |
| 5 | Agent COO | `05-coo/` | The **Chief Operating Officer** agent (read-only operational analyst): a narrated 7-beat arc — grounded operational review with planning + a subagent rail deep-dive, a derived figure via the compute tool, durable memory recalled in a fresh thread, scope refusals, and a caveated float. Ships a one-command in-cluster runner + a live console. See `05-coo/README.md`. |
| 6 | Agent CFO | `06-cfo/` | The **Chief Financial Officer** agent (read-only financial analyst on the same `csuite` harness): a 7-beat arc — grounded period review with planning + a subagent segment deep-dive, a cost-to-income ratio via compute, durable memory, refusing an unverifiable NPL premise, scope discipline (operations are the COO's), and period discipline (no fabricated quarter). One-command runner + live console. See `06-cfo/README.md`. |
| 8 | Agent CTO | `08-cto/` | The **Chief Technology Officer** agent (platform analyst + operator on the `csuite` harness): a 7-beat arc across BOTH kind clusters — grounded reliability/delivery review with planning + a subagent deep-dive, a degraded-share figure via compute, durable memory, scope discipline (the books are the CFO's), a **restart refused** on a healthy target (the self-verify guardrail), and an **autonomous rollback** that genuinely recovers a staged bad rollout on `cfo` — every action audited in the tamper-evident ledger. One-command runner + a two-pane **presentation console** (agent arc + live ledger, run-live/replay) in `08-cto/present/`. See `08-cto/README.md` and `08-cto/present/README.md`. |

_More demos ahead (each gets its own numbered `demos/NN-<name>/`)._

Demos 5–6 and 8 are a different shape from 1–4: not a single `app.py` over the bank API
but a narrated `/ask` driver (`NN-<agent>/run-demo.sh` → `drive.py`, sharing
`demos/_driver.py`) plus the agent console
(`coo/console.py`). Its README carries the full runbook and question sheet.

Demos are independent Streamlit apps — run several at once on different ports
(e.g. demo 1 on `:8510`, demo 2 on `:8511`), all pointing at the same bank API.

## Running a demo

Demos talk to the bank API. In the k8s setup the API isn't published to the host,
so port-forward it first:

```bash
kubectl --context kind-nano-bank -n nano-bank port-forward svc/bank-api 8081:8081
```

Then run the demo (point `DEMO_API_BASE` elsewhere if your API is not on
`localhost:8081`):

```bash
pip install -r demos/01-onboarding/requirements.txt
DEMO_API_BASE=http://localhost:8081 streamlit run demos/01-onboarding/app.py
```

(If you run the bank as a host `cargo run`, it's already on `http://localhost:8081`
and no port-forward is needed.)

Demo 3 talks to the **manager Branch API** (`:8086`), not the bank API directly:

```bash
kubectl --context kind-nano-bank -n nano-bank port-forward svc/agent-api 8086:8086
TOKEN=$(grep -E '^BRANCH_SERVICE_TOKEN=' agent/.env | cut -d= -f2-)
DEMO_BRANCH_BASE=http://localhost:8086 DEMO_BRANCH_TOKEN=$TOKEN \
  streamlit run demos/03-agentic-manager/app.py
```

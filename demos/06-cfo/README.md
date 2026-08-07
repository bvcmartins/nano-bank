# Agent CFO — demo

A narrated walk-through of the nano-bank Chief Financial Officer: a read-only
financial analyst that answers **only** from its finance tools, plans and
delegates multi-step reviews, grounds every figure against a tool result, is
strict about periods, and refuses premises it cannot verify. Same shared harness
as the COO (`csuite`), over the finance MCP instead of operations.

Two ways to run it, same story:

- **Narrated script** (`run-demo.sh` → `drive.py`) — reproducible; brings the
  stack up, seeds the bank + closes a couple of period snapshots, and prints each
  beat with the answer, the verification badge, and the harness trace highlights.
- **Live console** (`cfo/console.py`) — a Streamlit chat you drive by hand; type
  the beats and watch the answer, the grounding badge, and the expandable
  **harness trace** panel (plan / todos / subagent / memory) update live.

Everything here is a demo: it only ever **asks** the CFO (whose one
state-changing tool, `close_period`, captures a period-end GL snapshot; the seed
uses it to give reviews something to read).

## Run it (in-cluster)

```bash
# up (modern core + bank + agent + CFO) → seed + close periods → narrated arc
demos/06-cfo/run-demo.sh

demos/06-cfo/run-demo.sh --no-up       # stack already deployed
demos/06-cfo/run-demo.sh --no-seed     # leave data/periods as-is
demos/06-cfo/run-demo.sh --beats 1,5   # only these beats
demos/06-cfo/run-demo.sh --down        # tear down the demo port-forwards
```

Prereqs: `docker` + `kind` + `kubectl` + `uv`, and the sibling
`nano-bank-modern-core` repo. Bring-up leans on `scripts/deploy-all.sh` +
`cfo/k8s/deploy.sh`.

### Live console

`run-demo.sh` tears down its own port-forwards when it exits, so open one
yourself (the stack stays up in-cluster):

```bash
kubectl -n nano-bank port-forward svc/cfo 8089:8089 &
CFO_API_URL=http://localhost:8089 streamlit run cfo/console.py   # :8501
```

Then type the beats from `questions.md`.

## The arc — what each beat proves

| # | Ask | What it demonstrates |
|---|-----|----------------------|
| 1 | Close the period + a financial health review, deep-dive one segment | **Grounded reads** across balance sheet / income / NIM / ratios / RAROC; the harness **plans**, keeps **todos**, and **spawns a subagent**; honest about `close_period` |
| 2 | "Cost-to-income ratio for this month?" | A **derived figure** no metric tool returns: the CFO pulls the components and calls **`compute`** to make the ratio, grounded |
| 3 | "Record the biggest financial risk" | Durable **memory write** |
| 4 | *(fresh conversation)* "Recall that note; where to focus?" | **Memory recall across turns** from durable Qdrant memory |
| 5 | "Our 3% NPL ratio — what's driving it?" | **Refuses an unverifiable premise** — the ledger has no NPL data, so it declines rather than completing the narrative |
| 6 | "How's the settlement backlog / rail throughput?" | **Scope discipline** — operations are the COO's domain |
| 7 | "How did we do last quarter?" | **Period discipline** — snapshots are monthly; it names the periods that exist and won't fabricate a quarter |

## Notes

- The **memory** beat needs Qdrant up (deployed by the agent stack). If it's
  absent the CFO still answers from live tools — memory degrades to a no-op.
- The **first** request after a fresh deploy is slow: the CFO downloads its
  fastembed memory model on cold start. Warm requests stream in ~20s.
- `close_period` snapshots the **current** GL trial balance tagged with the
  period label, so period-over-period metrics reflect the state at snapshot time.
- The seed also **accrues interest** and posts a **loan book**
  (`testing/seed-loan-book.sh`) so NIM/RAROC are believable — nano-bank has no
  loan product, and a deposit-only bank shows a deeply negative NIM (interest
  expense on deposits, almost no earning assets). The loan book is a demo GL
  scaffold (aggregate `loans_receivable` + `interest_income`), not real loans.

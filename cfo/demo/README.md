# CFO demo

Two scripts: one brings the stack up, the other fills the bank with events so
the Agent CFO has a real balance sheet to talk about.

```bash
bash cfo/demo/run-cfo-stack.sh     # start everything, prints the console URL
bash cfo/demo/seed-demo-bank.sh    # reset the GL, generate the events, close the period
# → chat at http://localhost:8506
bash cfo/demo/run-cfo-stack.sh --stop
```

`seed-demo-bank.sh` resets and repopulates the GL on every run (via
`reset-gl.sh`) so the demo is reproducible — posting a second opening book on
top of the first would double the balance sheet. Pass `--keep-gl` to skip it.
The reset clears the **core's journal and `gl_snapshots` only**; nano-bank's own
Postgres — customers, accounts, mandates, rail history — is untouched.

## What comes up

| Process | Port | Notes |
|---|---|---|
| bank API | 8081 | run **from source** (`api/target/debug/nano-bank-api`) |
| modern core | 8191 | already running in the `modern-core` Kind cluster |
| finance MCP | 8088 | reads the core trial balance, owns the report math |
| CFO API | 8089 | `POST /ask`, `GET /health` |
| CFO console | 8506 | Streamlit chat |

Postgres must be reachable on `::1:5432` (the Kind port-forward) and
`agent/.env` must hold `OLLAMA_API_KEY`.

The bank API runs from source deliberately: the `bank-api` image deployed in the
`nano-bank` cluster predates the finance specs, so it has neither the expanded GL
chart nor `/api/v1/finance/*`. Redeploying that image would make the in-cluster
service work too.

## What "realistic" means here

The seeded bank is calibrated so the ratios land where a real challenger bank's
would. After a run you should see roughly:

| Ratio | Demo | Real banks |
|---|---|---|
| Leverage (equity/assets) | ~10% | 6–10% |
| Loan-to-deposit | ~73% | 70–90% |
| Efficiency ratio | ~60% | 55–70% |
| Cost of funds | 2.50% | 2–3% |
| Yield on earning assets | ~8.9% | 6–9% |
| RWA capital ratio | ~15% | 12–18% |
| NIM | ~6% | 2–4% (higher when card-heavy) |
| RAROC | ~15% | hurdle 12–20% |

**ROA (~2%) and ROE (~21%) read high on purpose.** The ledger has no loan-loss
provision account, so net income is *pre-provision* — credit cost enters through
RAROC's expected loss instead. Net of the $9.1k expected loss, ROE is ~10%,
which is the textbook figure. `raroc()` returns a `basis` field saying this, so
the CFO explains it correctly rather than guessing.

Two things make the numbers honest rather than hand-set: interest is derived
from annual rates over the real ACT/365 day count, and the opening balance sheet
is closed as the **prior** period so averages (earning assets, deposits) are
computed against a real opening book. Without that opening snapshot every
average is halved and NIM / cost of funds come out roughly double.

## What gets seeded

1. **Treasury desk** — capital injection, wholesale deposit funding, treasury
   placements, a consumer loan book, card + overdraft receivables.
2. **Retail customers** — 5 customers with chequing/savings/credit-card accounts,
   deposits, withdrawals, transfers.
3. **Card rails** — authorize → capture → settle (recognizes interchange income).
4. **Interac e-Transfer** — one send (recognizes fee income).
5. **Bank P&L** — treasury/loan/card interest earned, deposit funding cost, opex.
6. **Finance batches** — 10 days of daily interest accrual, then month
   capitalisation (deposit + card interest, maintenance fees).
7. **Period close** — snapshots the trial balance into `gl_snapshots`.

Steps 1 and 5 post through `POST /api/v1/ledger/journal` because no handler
originates treasury placements or a loan book yet — those GL roles arrived with
spec #1 and are driven by later specs. Everything else is real bank traffic
through the real handlers.

Tunable: `CUSTOMERS`, `ACCRUAL_DAYS`, `PERIOD`, `API`.

## Things to ask

- "Give me the financial health of the bank for 2026-07."
- "Where is our profit actually coming from? Which revenue lines are sustainable?"
- "What is our RAROC and is it above a sensible hurdle rate?"
- "Break down the P&L by segment — which product line earns its keep?"
- "How exposed are we if our cost of funds rises 200 bps?"

The CFO is read-only: it will analyse and recommend, but it cannot move money or
post entries.

### Segment P&L will not tie to the income statement

`segment_pnl` reads nano-bank's **operational** tables (`interest_accruals`,
`transactions`); every other report reads the **GL snapshot** from the core. The
reset here clears the GL only — nano-bank's own Postgres keeps its customers,
mandates and rail history, and with them every fee and accrual written by past
test-harness runs. So the segments carry activity the freshly-seeded GL never
booked: a demo whose GL nets $1,448 can show $10,918 of segment income, most of
it thousands of stale $4 maintenance fees.

That is a property of the reset scope, not a bug to seed around. `segment_pnl`
reconciles itself against the income statement and returns the gap, so the CFO
reports the discrepancy instead of ranking product lines on numbers that do not
tie. To see them agree, you would have to wipe nano-bank's transaction history
too — which costs you the rail and customer data the rest of the repo tests
against.

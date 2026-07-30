# `nano-bank-finance` Reporting Service — Design (Spec #3)

Status: approved (brainstorming), not yet implemented.

## Programme context

Spec #3 of the five-spec financial-reporting stack:

1. **Spec #1 — GL chart-of-accounts expansion** (built; PR #29 open).
2. **Spec #2 — interest / NIM engine** (built; PR #30). Produces the tagged
   income/expense postings and the accrual/capitalisation subledger this service
   reports on.
3. **Spec #3 — this doc.** A standalone Python **reporting service** that reads
   nano-bank's Postgres and exposes period financial reports as **MCP tools**.
4. Spec #4 — per-transaction cost/profit attribution and FTP.
5. Spec #5 — Economic Capital and RAROC.

### Vision: an autonomous bank

nano-bank is being built to run **autonomously**. The reports this service
produces are consumed **directly by an Agent CFO** (a separate, later spec), which
makes decisions and takes the actions. Humans converse with the agent through an
interface but do not act on the bank themselves, and there is **no human
dashboard**. That is why this service's interface is an **MCP tool surface** (not
a UI) and why the report math must be deterministic and reconciled to the GL: an
autonomous agent needs trustworthy, machine-consumable numbers.

## Decisions locked during brainstorming

- **Data source:** read nano-bank's Postgres directly (like `agent/db.py`).
- **Reports (monthly + yearly):** Balance Sheet, Income Statement, NIM, and
  **segment P&L** (by `product` / `cost_centre`, using the spec #2 tags).
- **Period model:** **period-close snapshots**, owned by this service.
- **Interface:** an **MCP server** exposing each report as a tool (like
  `agent/mcp_server.py`). No FastAPI-for-humans, no dashboard.
- **Scope:** the reporting service only — the Agent CFO is a separate spec.
- **Placement:** a new `finance/` directory in the nano-bank repo, parallel to
  `agent/`; runs in-cluster with its own Dockerfile + k8s manifests.

## Background: where the numbers live

- **Tagged flows** — interest (`interest_accruals`, per-account per-day, with
  `side`, `product`, `cost_centre`) and fees (`transactions` where
  `transaction_type='fee'`, with `product`/`cost_centre`) — live in nano-bank's
  Postgres.
- **Role-level balances** and **interchange income** live only in the **core GL**
  (nano-bank posts aggregate role balances there; interchange is a GL-only post
  with no local subledger row). nano-bank exposes the current trial balance at
  `GET /api/v1/ledger/balances` (backend-specific account codes).

The **period-close snapshot** bridges these two worlds.

## 1. Period-close snapshots

`close_period(period)` (period = `YYYY-MM`):

1. Reads nano-bank's `/ledger/balances` (the core GL trial balance).
2. Maps each backend account code → **semantic role** via an embedded reverse map
   (the same 20-odd accounts from specs #1–#2, keyed by both modern code and
   legacy saknr), so snapshots are **backend-agnostic**.
3. Writes `gl_snapshots(period, role, balance, captured_at)` — a table this
   service **owns and self-heals** (idempotent `CREATE TABLE IF NOT EXISTS` on
   startup) in nano-bank's Postgres. **No Rust / DDL change to nano-bank.**

Re-closing a period overwrites its snapshot (idempotent, `PRIMARY KEY(period,
role)`). A Balance Sheet is reproducible once its period is closed; an Income
Statement for a period needs that period's close **and** the prior period's close
(for the opening balances).

## 2. Report computations

All money is `Decimal`; all reads are read-only.

- **Balance Sheet (as-of `period`):** classify the period's snapshot roles into
  asset / liability / equity lines and total each; assert **A = L + E**.
- **Income Statement (`period`):** the period flow of each income/expense role is
  `closing snapshot − prior-period-close snapshot`. GL accounts accumulate (no
  nominal close-to-retained), so the delta is the period's flow — and it includes
  interchange. Income roles: `InterestIncome`, `InterchangeIncome`, `FeeIncome`;
  expense roles: `InterestExpense`, `OperatingExpense`. Net income = income −
  expense.
- **NIM (`period`):** `(InterestIncome − InterestExpense) / average earning
  assets`, annualised by period length. Earning assets = the **interest-bearing**
  asset roles (`CardReceivable`, `OverdraftReceivable`, `LoansReceivable`,
  `TreasuryPlacement`) — `CashReserves` is excluded, it earns nothing in this
  model and would dilute the denominator; average = mean of the opening and
  closing snapshot balances.
- **Segment P&L (`period`):** income/expense by `product` and `cost_centre`:
  - interest from `interest_accruals` grouped by `product`/`cost_centre` over the
    period;
  - fees from `transactions` (`transaction_type='fee'`) grouped by
    `product`/`cost_centre`;
  - **interchange** (GL-only, no subledger row) is taken as its Income-Statement
    total and attributed 100 % to `product=card` / `cost_centre=payments` (all
    interchange is card/payments by construction).
  Segment totals **reconcile** to the Income Statement's income/expense figures.

**Yearly** reports take a `year` (`YYYY`): the Balance Sheet uses the Dec close;
the Income Statement / NIM / segment P&L roll up the year (Jan opening → Dec
closing, or the sum of monthly flows).

## 3. MCP tool surface (`finance/mcp_server.py`, FastMCP)

Each report is a tool returning structured JSON:

- `close_period(period)` — capture/refresh the snapshot; returns a summary.
- `list_periods()` — periods with a snapshot available.
- `balance_sheet(period)` — assets / liabilities / equity + totals.
- `income_statement(period)` — income / expense lines + net income.
- `nim(period)` — net interest, average earning assets, margin.
- `segment_pnl(period)` — income/expense by product and cost-centre.

`period` accepts `YYYY-MM` (month) or `YYYY` (year roll-up). Served over
streamable-HTTP like `agent/`, on the **bank/CFO plane** (bank aggregates, no
customer PII).

## 4. Code layout (`finance/`)

- `config.py` — settings (DB params, nano-bank base URL, service token).
- `db.py` — read-only psycopg2 access (RealDictCursor) + the single `gl_snapshots`
  write and its self-heal DDL; injectable for tests (like `agent/db.py`).
- `ledger_client.py` — HTTP client for `/ledger/balances`.
- `roles.py` — the backend-code → semantic-role reverse map + role → statement
  classification (asset/liability/equity/income/expense, earning-asset flag).
- `snapshots.py` — `close_period`.
- `reports.py` — **pure** report math (balance_sheet / income_statement / nim /
  segment_pnl), taking injected data so it is unit-testable without a DB.
- `mcp_server.py` — the FastMCP tools wrapping `snapshots` + `reports`.
- `requirements.txt`, `Dockerfile`, `k8s/` — in-cluster deployment like `agent/`.
- `tests/` — pytest on the report math with fixture rows, plus a live smoke that
  closes a period and reads each report against a running nano-bank.

## 5. Auth & runtime

Bank/CFO plane — reports are bank aggregates with no customer PII. v1 uses a
shared service token / trusted in-cluster network; fine-grained CFO auth is
deferred to the Agent CFO spec. The service runs in-cluster; `gl_snapshots`
self-heals on startup. Reads use a read-only DB session; the only write is the
snapshot upsert.

## 6. Testing / done criteria

Done when:

1. `close_period` captures a backend-agnostic snapshot from `/ledger/balances`
   into `gl_snapshots`, idempotently, against **both** `CORE_BACKEND=modern` and
   `legacy`.
2. Each report tool returns correct figures on fixture data: Balance Sheet
   balances (A = L + E); Income Statement net income = income − expense from
   snapshot deltas; NIM = net interest / avg earning assets; segment P&L
   reconciles to the Income Statement (including interchange attributed to
   card/payments).
3. Unit tests cover the report math (pure functions, fixture rows) and the
   role-classification map; a live smoke closes a period and reads each report.
4. The MCP server starts and lists the six tools.

## 7. Out of scope (later specs)

- The **Agent CFO** itself — the consumer that decides and acts (its own spec).
- Per-transaction cost/profit attribution and FTP (spec #4).
- Economic Capital and RAROC (spec #5).
- A human dashboard / UI — deliberately none (autonomous-bank vision).
- Fine-grained CFO authorization and any write/action endpoints — this service is
  read + snapshot only.

## Open questions

None blocking. One implementation-time note: `list_periods`/roll-ups assume
month-grain snapshots; a period with no prior close reports its Income Statement
against a zero opening (first period) — acceptable and made explicit.

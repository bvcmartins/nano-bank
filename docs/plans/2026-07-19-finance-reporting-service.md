# `nano-bank-finance` Reporting Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Python service (`finance/`) that reads nano-bank's Postgres, captures period-close GL snapshots, and exposes Balance Sheet / Income Statement / NIM / segment-P&L reports as MCP tools for an autonomous Agent CFO.

**Architecture:** Pure report math (`reports.py`) + a backend-agnostic role map (`roles.py`) sit under a thin DB/HTTP/IO layer (`db.py`, `ledger_client.py`, `snapshots.py`) and a FastMCP tool surface (`mcp_server.py`). Period-close snapshots (service-owned `gl_snapshots` table in nano-bank's Postgres) bridge the core GL trial balance to the tagged subledger. Runs in-cluster like `agent/` (Dockerfile + k8s), MCP over streamable-HTTP. No dashboard, no write/action endpoints.

**Tech Stack:** Python 3.12, `mcp` (FastMCP), `psycopg2-binary`, `httpx`, `uvicorn`, `pytest`. Mirrors the `agent/` service conventions.

## Global Constraints

- The service is **read + snapshot only** — no actions, no customer-mutating writes. The only write is the `gl_snapshots` upsert.
- Money is `decimal.Decimal` end to end; never `float`. Serialize as strings in tool output.
- DB access is **read-only** psycopg2 (`conn.set_session(readonly=True)`) except the dedicated snapshot-writer connection.
- DB host is **`::1`** locally (IPv6 loopback); in-cluster it is `postgres-service`. DB name `nano_bank_db`, user `nanobank_user` (see `agent/config.py` / `agent/k8s/mcp.yaml`).
- Canonical GL sign convention inside the service: **debit − credit** (assets/expenses positive; liabilities/equity/income negative). `snapshots.py` stores balances in this convention; **verify the core's `/ledger/balances` sign at implementation time** (Task 4, Step 2) and normalize on ingest if the core returns magnitudes.
- Semantic roles and their codes come from specs #1–#2 (see the role table in Task 1). `period` is `YYYY-MM` (month) or `YYYY` (year roll-up).
- Follow `agent/` conventions exactly: `Settings.from_env`, injectable `db` for tests, `FastMCP(..., transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`, `BindMiddleware`-style app, `python -m finance.mcp_server`.
- Branch: `finance-reporting-service` (already created off `finance-nim-engine`).

---

### Task 1: Scaffold + role map (`roles.py`, pure, unit-tested)

**Files:**
- Create: `finance/__init__.py`, `finance/config.py`, `finance/roles.py`, `finance/requirements.txt`
- Test: `finance/tests/__init__.py`, `finance/tests/test_roles.py`

**Interfaces:**
- Produces: `roles.role_for_code(code: str) -> str | None`; `roles.STATEMENT_LINE: dict[str,str]` (role → `asset|liability|equity|income|expense`); `roles.EARNING_ASSET_ROLES: set[str]`; `roles.INCOME_ROLES`, `roles.EXPENSE_ROLES: set[str]`.

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_roles.py`:

```python
from finance import roles

def test_reverse_map_covers_both_backends():
    assert roles.role_for_code("INT_INCOME") == "InterestIncome"
    assert roles.role_for_code("0000800100") == "InterestIncome"   # legacy saknr
    assert roles.role_for_code("ACCR_INT_PAY") == "AccruedInterestPayable"
    assert roles.role_for_code("0000220000") == "AccruedInterestPayable"
    assert roles.role_for_code("UNKNOWN") is None

def test_statement_classification():
    assert roles.STATEMENT_LINE["CardReceivable"] == "asset"
    assert roles.STATEMENT_LINE["CustomerDeposits"] == "liability"
    assert roles.STATEMENT_LINE["Capital"] == "equity"
    assert roles.STATEMENT_LINE["InterchangeIncome"] == "income"
    assert roles.STATEMENT_LINE["InterestExpense"] == "expense"

def test_earning_assets_exclude_cash_reserves():
    assert "CashReserves" not in roles.EARNING_ASSET_ROLES
    assert roles.EARNING_ASSET_ROLES == {
        "CardReceivable", "OverdraftReceivable", "LoansReceivable", "TreasuryPlacement",
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance && python -m pytest tests/test_roles.py -q`
Expected: FAIL (module `finance.roles` not found).

- [ ] **Step 3: Write `roles.py`**

Create `finance/roles.py`:

```python
"""Backend-agnostic GL role map + statement classification (specs #1-#2).

`/ledger/balances` returns backend-specific codes (modern code or legacy saknr);
this maps both to the semantic role, and the role to its financial-statement line.
"""
from __future__ import annotations

# role -> (modern code, legacy saknr)
_ROLE_CODES: dict[str, tuple[str, str]] = {
    "Bank": ("BANK", "0000113100"),
    "Receivable": ("AR", "0000140000"),
    "Payable": ("AP", "0000160000"),
    "Revenue": ("REVENUE", "0000800000"),
    "Expense": ("EXPENSE", "0000400000"),
    "CashReserves": ("CASH_RESERVES", "0000105000"),
    "CardReceivable": ("CARD_AR", "0000141000"),
    "OverdraftReceivable": ("OVERDRAFT_AR", "0000141500"),
    "LoansReceivable": ("LOANS_AR", "0000142000"),
    "TreasuryPlacement": ("TREASURY", "0000150000"),
    "CustomerDeposits": ("DEPOSITS", "0000210000"),
    "Capital": ("CAPITAL", "0000300000"),
    "RetainedEarnings": ("RETAINED", "0000330000"),
    "InterestIncome": ("INT_INCOME", "0000800100"),
    "InterchangeIncome": ("INTERCHANGE", "0000800200"),
    "FeeIncome": ("FEE_INCOME", "0000800300"),
    "InterestExpense": ("INT_EXPENSE", "0000400100"),
    "OperatingExpense": ("OPEX", "0000400200"),
    "AccruedInterestReceivable": ("ACCR_INT_RECV", "0000141900"),
    "AccruedInterestPayable": ("ACCR_INT_PAY", "0000220000"),
}

_CODE_TO_ROLE: dict[str, str] = {}
for _role, (_m, _l) in _ROLE_CODES.items():
    _CODE_TO_ROLE[_m] = _role
    _CODE_TO_ROLE[_l] = _role


def role_for_code(code: str) -> str | None:
    """Semantic role for a backend GL code, or None if unrecognized."""
    return _CODE_TO_ROLE.get(code)


STATEMENT_LINE: dict[str, str] = {
    "Bank": "asset", "Receivable": "asset", "CashReserves": "asset",
    "CardReceivable": "asset", "OverdraftReceivable": "asset",
    "LoansReceivable": "asset", "TreasuryPlacement": "asset",
    "AccruedInterestReceivable": "asset",
    "Payable": "liability", "CustomerDeposits": "liability",
    "AccruedInterestPayable": "liability",
    "Capital": "equity", "RetainedEarnings": "equity",
    "Revenue": "income", "InterestIncome": "income",
    "InterchangeIncome": "income", "FeeIncome": "income",
    "Expense": "expense", "InterestExpense": "expense",
    "OperatingExpense": "expense",
}

EARNING_ASSET_ROLES: set[str] = {
    "CardReceivable", "OverdraftReceivable", "LoansReceivable", "TreasuryPlacement",
}
INCOME_ROLES: set[str] = {r for r, l in STATEMENT_LINE.items() if l == "income"}
EXPENSE_ROLES: set[str] = {r for r, l in STATEMENT_LINE.items() if l == "expense"}
```

- [ ] **Step 4: Write `config.py`, `requirements.txt`, package files**

Create `finance/__init__.py` (empty), `finance/tests/__init__.py` (empty).

Create `finance/requirements.txt`:

```
mcp>=1.2
psycopg2-binary>=2.9
httpx>=0.27
uvicorn>=0.30
pytest>=8.0
```

Create `finance/config.py` (mirror `agent/config.py`):

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    db: dict
    nano_bank_api: str
    mcp_port: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            db=dict(
                host=g("DB_HOST", "::1"),
                port=int(g("DB_PORT", "5432")),
                dbname=g("DB_NAME", "nano_bank_db"),
                user=g("DB_USER", "nanobank_user"),
                password=g("DB_PASSWORD", "secure_nano_password_2024!"),
            ),
            nano_bank_api=g("NANO_BANK_API", "http://localhost:8081"),
            mcp_port=int(g("MCP_PORT", "8088")),
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd finance && python -m pytest tests/test_roles.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add finance/__init__.py finance/config.py finance/roles.py finance/requirements.txt finance/tests
git commit -m "feat(finance): scaffold reporting service + GL role map"
```

---

### Task 2: Pure report math (`reports.py`, unit-tested)

**Files:**
- Create: `finance/reports.py`
- Test: `finance/tests/test_reports.py`

**Interfaces:**
- Consumes: `finance.roles`.
- Produces (all pure; balances in debit−credit convention as `Decimal`; `snapshot` = `dict[role, Decimal]`):
  - `balance_sheet(snapshot: dict) -> dict` — `{assets, liabilities, equity, total_assets, total_liabilities_equity, balanced: bool}`.
  - `income_statement(closing: dict, opening: dict) -> dict` — `{income: {role: amt}, expense: {role: amt}, total_income, total_expense, net_income}` (period flow = −(closing−opening) for credit-normal income, (closing−opening) for expense; presented positive-natural).
  - `nim(closing: dict, opening: dict, days: int) -> dict` — `{net_interest, avg_earning_assets, nim}`.
  - `segment_pnl(accruals: list[dict], fees: list[dict], interchange_total: Decimal) -> dict` — income/expense keyed by `(product, cost_centre)`.

- [ ] **Step 1: Write the failing tests**

Create `finance/tests/test_reports.py`:

```python
from decimal import Decimal as D
from finance import reports

# Balances in debit-credit convention: assets/expenses +, liab/equity/income -.
def _snapshot(**kw):
    return {k: D(v) for k, v in kw.items()}

def test_balance_sheet_balances():
    snap = _snapshot(
        CashReserves="1000", CardReceivable="500",   # assets +1500
        CustomerDeposits="-1400",                     # liability 1400
        Capital="-100",                               # equity 100
    )
    bs = reports.balance_sheet(snap)
    assert bs["total_assets"] == D("1500")
    assert bs["total_liabilities_equity"] == D("1500")
    assert bs["balanced"] is True

def test_income_statement_period_flow():
    opening = _snapshot(InterestIncome="-100", InterestExpense="20", FeeIncome="-5")
    closing = _snapshot(InterestIncome="-130", InterestExpense="26", FeeIncome="-8")
    inc = reports.income_statement(closing, opening)
    # income flow: InterestIncome 30, FeeIncome 3 -> 33; expense 6; net 27
    assert inc["total_income"] == D("33")
    assert inc["total_expense"] == D("6")
    assert inc["net_income"] == D("27")

def test_nim():
    opening = _snapshot(CardReceivable="1000", InterestIncome="0", InterestExpense="0")
    closing = _snapshot(CardReceivable="1000", InterestIncome="-30", InterestExpense="6")
    out = reports.nim(closing, opening, days=30)
    assert out["net_interest"] == D("24")            # 30 income - 6 expense
    assert out["avg_earning_assets"] == D("1000")
    # annualised: 24/1000 * 365/30
    assert out["nim"] == (D("24") / D("1000") * (D("365") / D("30")))

def test_segment_pnl_reconciles_with_interchange():
    accruals = [
        {"product": "deposit", "cost_centre": "deposits", "side": "expense", "amount": D("6")},
        {"product": "card", "cost_centre": "lending", "side": "income", "amount": D("30")},
    ]
    fees = [{"product": "payment", "cost_centre": "payments", "amount": D("3")}]
    out = reports.segment_pnl(accruals, fees, interchange_total=D("12"))
    # card/payments gets the interchange 12; total income = 30 + 3 + 12 = 45
    assert out["total_income"] == D("45")
    assert out["total_expense"] == D("6")
    assert out["segments"][("card", "payments")]["income"] == D("12")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd finance && python -m pytest tests/test_reports.py -q`
Expected: FAIL (`finance.reports` not found).

- [ ] **Step 3: Write `reports.py`**

Create `finance/reports.py`:

```python
"""Pure period-report math. All inputs are plain data (dicts/lists of Decimal);
no DB or IO here, so every function is unit-testable in isolation. Balances use
the debit-credit convention (assets/expenses +, liabilities/equity/income -).
"""
from __future__ import annotations
from decimal import Decimal
from . import roles


def _line_total(snapshot: dict, line: str, *, credit_normal: bool) -> dict:
    out = {}
    for role, bal in snapshot.items():
        if roles.STATEMENT_LINE.get(role) != line:
            continue
        out[role] = -bal if credit_normal else bal
    return out


def balance_sheet(snapshot: dict) -> dict:
    assets = _line_total(snapshot, "asset", credit_normal=False)
    liabilities = _line_total(snapshot, "liability", credit_normal=True)
    equity = _line_total(snapshot, "equity", credit_normal=True)
    ta = sum(assets.values(), Decimal(0))
    tle = sum(liabilities.values(), Decimal(0)) + sum(equity.values(), Decimal(0))
    return {
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "total_assets": ta, "total_liabilities_equity": tle,
        "balanced": ta == tle,
    }


def _flow(closing: dict, opening: dict, role: str, *, credit_normal: bool) -> Decimal:
    delta = closing.get(role, Decimal(0)) - opening.get(role, Decimal(0))
    return -delta if credit_normal else delta


def income_statement(closing: dict, opening: dict) -> dict:
    income = {r: _flow(closing, opening, r, credit_normal=True) for r in roles.INCOME_ROLES
              if r in closing or r in opening}
    expense = {r: _flow(closing, opening, r, credit_normal=False) for r in roles.EXPENSE_ROLES
               if r in closing or r in opening}
    ti = sum(income.values(), Decimal(0))
    te = sum(expense.values(), Decimal(0))
    return {"income": income, "expense": expense,
            "total_income": ti, "total_expense": te, "net_income": ti - te}


def nim(closing: dict, opening: dict, days: int) -> dict:
    net_interest = (_flow(closing, opening, "InterestIncome", credit_normal=True)
                    - _flow(closing, opening, "InterestExpense", credit_normal=False))
    avg = Decimal(0)
    for role in roles.EARNING_ASSET_ROLES:
        avg += (opening.get(role, Decimal(0)) + closing.get(role, Decimal(0))) / Decimal(2)
    margin = (net_interest / avg * (Decimal(365) / Decimal(days))) if avg else Decimal(0)
    return {"net_interest": net_interest, "avg_earning_assets": avg, "nim": margin}


def segment_pnl(accruals: list, fees: list, interchange_total: Decimal) -> dict:
    segments: dict[tuple, dict] = {}

    def bucket(product, cost_centre):
        return segments.setdefault((product, cost_centre), {"income": Decimal(0), "expense": Decimal(0)})

    for a in accruals:
        b = bucket(a["product"], a["cost_centre"])
        b["income" if a["side"] == "income" else "expense"] += a["amount"]
    for f in fees:
        bucket(f["product"], f["cost_centre"])["income"] += f["amount"]
    if interchange_total:
        bucket("card", "payments")["income"] += interchange_total

    ti = sum(s["income"] for s in segments.values())
    te = sum(s["expense"] for s in segments.values())
    return {"segments": segments, "total_income": ti, "total_expense": te,
            "net_income": ti - te}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd finance && python -m pytest tests/test_reports.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add finance/reports.py finance/tests/test_reports.py
git commit -m "feat(finance): pure Balance Sheet/Income Statement/NIM/segment-P&L math"
```

---

### Task 3: DB access + snapshot schema (`db.py`)

**Files:**
- Create: `finance/db.py`
- Test: `finance/tests/test_db.py`

**Interfaces:**
- Produces `FinanceDB`:
  - `ensure_schema()` — idempotent `CREATE TABLE IF NOT EXISTS gl_snapshots(period TEXT, role TEXT, balance NUMERIC, captured_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY(period, role))`.
  - `write_snapshot(period: str, balances: dict[str, Decimal])` — upsert rows.
  - `read_snapshot(period: str) -> dict[str, Decimal]`.
  - `list_periods() -> list[str]`.
  - `accruals(start, end) -> list[dict]` — `product, cost_centre, side, SUM(amount) amount` from `interest_accruals` where `accrual_date` in `[start, end)`.
  - `fees(start, end) -> list[dict]` — `product, cost_centre, SUM(amount) amount` from `transactions` where `transaction_type='fee'` and `created_at` in range.
- `_rows` is overridable in tests (like `agent/db.py`).

- [ ] **Step 1: Write the failing test (fake `_rows`)**

Create `finance/tests/test_db.py`:

```python
from decimal import Decimal as D
from finance.db import FinanceDB

class FakeDB(FinanceDB):
    def __init__(self, canned):
        super().__init__(db_params=None)
        self._canned = canned
        self.writes = []
    def _rows(self, sql, params):
        return self._canned.get(sql.split("\n", 1)[0].strip(), [])
    def _exec(self, sql, params):
        self.writes.append((sql.split("\n",1)[0].strip(), params))

def test_read_snapshot_shapes_dict():
    db = FakeDB({"-- read_snapshot": [
        {"role": "CashReserves", "balance": D("1000")},
        {"role": "CustomerDeposits", "balance": D("-1000")},
    ]})
    snap = db.read_snapshot("2026-07")
    assert snap == {"CashReserves": D("1000"), "CustomerDeposits": D("-1000")}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance && python -m pytest tests/test_db.py -q`
Expected: FAIL (`finance.db` not found).

- [ ] **Step 3: Write `db.py`**

Create `finance/db.py` (mirror `agent/db.py` read pattern; add a writer path):

```python
from __future__ import annotations
from decimal import Decimal
from typing import Optional


class FinanceDB:
    """Read-only access to nano-bank's Postgres + the one gl_snapshots writer."""

    def __init__(self, db_params: Optional[dict] = None):
        self._db = db_params

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(**self._db)
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _exec(self, sql: str, params: tuple) -> None:
        import psycopg2
        conn = psycopg2.connect(**self._db)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        self._exec(
            "-- ensure_schema\n"
            "CREATE TABLE IF NOT EXISTS gl_snapshots ("
            " period TEXT NOT NULL, role TEXT NOT NULL, balance NUMERIC(20,2) NOT NULL,"
            " captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            " PRIMARY KEY (period, role))", ())

    def write_snapshot(self, period: str, balances: dict) -> None:
        for role, bal in balances.items():
            self._exec(
                "-- write_snapshot\n"
                "INSERT INTO gl_snapshots (period, role, balance) VALUES (%s,%s,%s) "
                "ON CONFLICT (period, role) DO UPDATE SET balance = EXCLUDED.balance,"
                " captured_at = now()", (period, role, bal))

    def read_snapshot(self, period: str) -> dict:
        rows = self._rows(
            "-- read_snapshot\nSELECT role, balance FROM gl_snapshots WHERE period = %s",
            (period,))
        return {r["role"]: Decimal(str(r["balance"])) for r in rows}

    def list_periods(self) -> list:
        rows = self._rows(
            "-- list_periods\nSELECT DISTINCT period FROM gl_snapshots ORDER BY period", ())
        return [r["period"] for r in rows]

    def accruals(self, start: str, end: str) -> list:
        return self._rows(
            "-- accruals\nSELECT product, cost_centre, side, SUM(amount) AS amount "
            "FROM interest_accruals WHERE accrual_date >= %s AND accrual_date < %s "
            "GROUP BY product, cost_centre, side", (start, end))

    def fees(self, start: str, end: str) -> list:
        return self._rows(
            "-- fees\nSELECT product, cost_centre, SUM(amount) AS amount "
            "FROM transactions WHERE transaction_type = 'fee' "
            "AND created_at >= %s AND created_at < %s "
            "AND product IS NOT NULL GROUP BY product, cost_centre", (start, end))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd finance && python -m pytest tests/test_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance/db.py finance/tests/test_db.py
git commit -m "feat(finance): DB access + gl_snapshots schema and readers"
```

---

### Task 4: Ledger client + period close (`ledger_client.py`, `snapshots.py`)

**Files:**
- Create: `finance/ledger_client.py`, `finance/snapshots.py`
- Test: `finance/tests/test_snapshots.py`

**Interfaces:**
- Consumes: `roles.role_for_code`, `FinanceDB.write_snapshot`.
- Produces: `ledger_client.get_balances(base_url) -> list[dict]` (`[{account, balance}]`); `snapshots.close_period(period, balances_rows, db) -> dict` — maps codes→roles (skipping unrecognized), stores the debit−credit balance, returns `{period, roles_captured}`.

- [ ] **Step 1: Determine the core's sign convention (one-time check)**

With a stack up (see Task 7), inspect the sign of a known credit-normal account:

Run: `curl -s localhost:8081/api/v1/ledger/balances | python3 -m json.tool | head -40`
Expected: confirm whether `CustomerDeposits`/`DEPOSITS` (a liability with a credit balance) comes back **negative** (debit−credit convention — the plan's assumption) or as a positive magnitude. If magnitudes, negate credit-normal roles in `close_period` (Step 3 notes where).

- [ ] **Step 2: Write the failing test**

Create `finance/tests/test_snapshots.py`:

```python
from decimal import Decimal as D
from finance import snapshots

class RecorderDB:
    def __init__(self): self.written = None
    def write_snapshot(self, period, balances): self.written = (period, balances)

def test_close_period_maps_codes_to_roles():
    rows = [
        {"account": "CASH_RESERVES", "balance": "1000.00"},
        {"account": "DEPOSITS", "balance": "-1000.00"},
        {"account": "MYSTERY", "balance": "5.00"},   # unrecognized -> skipped
    ]
    db = RecorderDB()
    out = snapshots.close_period("2026-07", rows, db)
    period, balances = db.written
    assert period == "2026-07"
    assert balances == {"CashReserves": D("1000.00"), "CustomerDeposits": D("-1000.00")}
    assert out["roles_captured"] == 2
```

- [ ] **Step 3: Write `ledger_client.py` and `snapshots.py`**

Create `finance/ledger_client.py`:

```python
from __future__ import annotations
import httpx

def get_balances(base_url: str) -> list:
    r = httpx.get(f"{base_url}/api/v1/ledger/balances", timeout=10.0)
    r.raise_for_status()
    return r.json()
```

Create `finance/snapshots.py`:

```python
"""Period-close: capture the core GL trial balance as a backend-agnostic snapshot."""
from __future__ import annotations
from decimal import Decimal
from . import roles


def close_period(period: str, balances_rows: list, db) -> dict:
    """Map /ledger/balances rows to semantic roles and store the snapshot.

    Balances are stored in debit-credit convention. If the one-time check in
    Task 4 Step 1 shows the core returns magnitudes, negate credit-normal roles
    here before storing.
    """
    snapshot: dict[str, Decimal] = {}
    for row in balances_rows:
        role = roles.role_for_code(row["account"])
        if role is None:
            continue
        snapshot[role] = Decimal(str(row["balance"]))
    db.write_snapshot(period, snapshot)
    return {"period": period, "roles_captured": len(snapshot)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd finance && python -m pytest tests/test_snapshots.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance/ledger_client.py finance/snapshots.py finance/tests/test_snapshots.py
git commit -m "feat(finance): period-close snapshot from the core trial balance"
```

---

### Task 5: MCP tool surface (`mcp_server.py`)

**Files:**
- Create: `finance/mcp_server.py`
- Test: `finance/tests/test_mcp.py`

**Interfaces:**
- Consumes: `Settings`, `FinanceDB`, `ledger_client`, `snapshots`, `reports`.
- Produces: `build_mcp(deps) -> FastMCP` with tools `close_period`, `list_periods`, `balance_sheet`, `income_statement`, `nim`, `segment_pnl`; `main()` serving streamable-HTTP on `settings.mcp_port`.

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_mcp.py`:

```python
import asyncio
from finance import mcp_server

class FakeDeps:
    class db:
        @staticmethod
        def list_periods(): return ["2026-06", "2026-07"]

def test_tools_registered():
    mcp = mcp_server.build_mcp(FakeDeps())
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"close_period", "list_periods", "balance_sheet",
            "income_statement", "nim", "segment_pnl"} <= names
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance && python -m pytest tests/test_mcp.py -q`
Expected: FAIL (`finance.mcp_server` not found).

- [ ] **Step 3: Write `mcp_server.py`**

Create `finance/mcp_server.py` (mirror `agent/mcp_server.py` serving). Helpers turn a `YYYY-MM`/`YYYY` period into its date range and prior period, and `str()` all Decimals in tool output. Sketch:

```python
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .db import FinanceDB
from . import ledger_client, snapshots, reports


@dataclass
class Deps:
    db: FinanceDB
    nano_bank_api: str


def _month_range(period: str):
    """('YYYY-MM') -> (start_iso, end_iso, prior_period, days)."""
    import calendar, datetime as dt
    y, m = (int(x) for x in period.split("-"))
    start = dt.date(y, m, 1)
    end = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    days = (end - start).days
    prior = f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"
    return start.isoformat(), end.isoformat(), prior, days


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {("|".join(map(str, k)) if isinstance(k, tuple) else k): _stringify(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def build_mcp(deps: Deps) -> FastMCP:
    mcp = FastMCP("nano-finance",
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=False))

    @mcp.tool()
    def close_period(period: str) -> dict:
        """Capture/refresh the GL trial-balance snapshot for a period (YYYY-MM)."""
        rows = ledger_client.get_balances(deps.nano_bank_api)
        return snapshots.close_period(period, rows, deps.db)

    @mcp.tool()
    def list_periods() -> list:
        """Periods with a snapshot available."""
        return deps.db.list_periods()

    @mcp.tool()
    def balance_sheet(period: str) -> dict:
        """Balance Sheet as of a closed period."""
        return _stringify(reports.balance_sheet(deps.db.read_snapshot(period)))

    @mcp.tool()
    def income_statement(period: str) -> dict:
        """Income Statement for a period (needs this period + the prior close)."""
        _, _, prior, _ = _month_range(period)
        return _stringify(reports.income_statement(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior)))

    @mcp.tool()
    def nim(period: str) -> dict:
        """Net interest margin for a period."""
        _, _, prior, days = _month_range(period)
        return _stringify(reports.nim(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior), days))

    @mcp.tool()
    def segment_pnl(period: str) -> dict:
        """P&L by product and cost-centre for a period."""
        start, end, prior, _ = _month_range(period)
        inc = reports.income_statement(deps.db.read_snapshot(period),
                                       deps.db.read_snapshot(prior))
        interchange = inc["income"].get("InterchangeIncome", Decimal(0))
        return _stringify(reports.segment_pnl(
            deps.db.accruals(start, end), deps.db.fees(start, end), interchange))

    return mcp


def main():
    settings = Settings.from_env()
    db = FinanceDB(settings.db)
    db.ensure_schema()
    mcp = build_mcp(Deps(db=db, nano_bank_api=settings.nano_bank_api))
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
```

Note: a `YYYY` (year) period is handled by extending `_month_range` to accept a 4-digit input (Jan 1 → next Jan 1, prior = `YYYY-1`); add that branch when wiring yearly roll-ups.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd finance && python -m pytest tests/test_mcp.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance/mcp_server.py finance/tests/test_mcp.py
git commit -m "feat(finance): MCP tool surface for the reports"
```

---

### Task 6: Container + k8s manifest

**Files:**
- Create: `finance/Dockerfile`, `finance/k8s/finance-mcp.yaml`

**Interfaces:** none (deployment).

- [ ] **Step 1: Write the Dockerfile**

Create `finance/Dockerfile` (mirror `agent/Dockerfile.mcp`):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/finance
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "finance.mcp_server"]
```

- [ ] **Step 2: Write the k8s manifest**

Create `finance/k8s/finance-mcp.yaml` (mirror `agent/k8s/mcp.yaml`, in the `nano-bank` namespace):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: finance-mcp
  namespace: nano-bank
  labels: { app: finance-mcp }
spec:
  replicas: 1
  selector: { matchLabels: { app: finance-mcp } }
  template:
    metadata: { labels: { app: finance-mcp } }
    spec:
      containers:
      - name: mcp
        image: nano-finance-mcp:dev
        imagePullPolicy: Never
        ports: [ { containerPort: 8088 } ]
        env:
        - { name: DB_HOST,       value: postgres-service }
        - { name: DB_PORT,       value: "5432" }
        - { name: DB_NAME,       value: nano_bank_db }
        - { name: DB_USER,       value: nanobank_user }
        - { name: DB_PASSWORD,   value: "secure_nano_password_2024!" }
        - { name: NANO_BANK_API, value: http://bank-api:8081 }
        - { name: MCP_PORT,      value: "8088" }
---
apiVersion: v1
kind: Service
metadata:
  name: finance-mcp
  namespace: nano-bank
spec:
  selector: { app: finance-mcp }
  ports: [ { port: 8088, targetPort: 8088 } ]
```

- [ ] **Step 3: Verify the manifest parses**

Run: `python3 -c "import yaml,sys; list(yaml.safe_load_all(open('finance/k8s/finance-mcp.yaml')))" && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add finance/Dockerfile finance/k8s/finance-mcp.yaml
git commit -m "feat(finance): container + k8s manifest for the reporting MCP"
```

---

### Task 7: Live cross-backend smoke

**Files:**
- Create: `finance/verify-reports.sh`

**Interfaces:** consumes a running nano-bank (`:8081`) against a started core, and the finance service's Python modules directly (no container needed for the smoke).

- [ ] **Step 1: Write the smoke script**

Create `finance/verify-reports.sh`: with a stack up (Kind Postgres + a core + nano-bank on :8081), and having generated some interest/fee/interchange activity (reuse `testing/verify-nim-engine.sh` to seed accrual + capitalisation + a card capture), then in Python:
- `FinanceDB(...).ensure_schema()`
- `close_period(period, ledger_client.get_balances(...), db)` for two consecutive periods (prior + current)
- print `balance_sheet`, `income_statement`, `nim`, `segment_pnl`
- assert `balance_sheet(...)["balanced"] is True` and `segment_pnl total_income == income_statement total_income`.

Use `DB_HOST=::1` and `NANO_BANK_API=http://localhost:8081`.

- [ ] **Step 2: Run against modern**

Run: `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share; cd finance && python -m pytest -q && CORE_BACKEND=modern bash verify-reports.sh`
Expected: unit tests PASS; smoke prints reports; Balance Sheet balanced; segment/IS reconcile.

- [ ] **Step 3: Run against legacy**

Run: `CORE_BACKEND=legacy bash finance/verify-reports.sh`
Expected: PASS (snapshots are backend-agnostic; figures reconcile).

- [ ] **Step 4: Commit**

```bash
git add finance/verify-reports.sh
git commit -m "test(finance): cross-backend reporting smoke"
```

---

## Self-Review

**Spec coverage:** §1 snapshots → Tasks 3-4; §2 report math (BS/IS/NIM/segment) → Task 2 (+ ranges/wiring in Task 5); §3 MCP tools → Task 5; §4 code layout → Tasks 1-6; §5 auth/runtime → Task 6 (in-cluster, self-heal in Task 5 `main`); §6 testing → per-task unit tests + Task 7 both-backend smoke. All covered.

**Placeholder scan:** No TBD/TODO. One deliberate implementation-time check (the core's balance sign convention, Task 4 Step 1) is concrete, with the exact command and the code location to adjust. Yearly roll-up is a noted extension point in Task 5 (monthly is fully specified).

**Type consistency:** `role_for_code`, `STATEMENT_LINE`, `EARNING_ASSET_ROLES`, `INCOME_ROLES`, `EXPENSE_ROLES` (Task 1) are used with those exact names in `reports.py` (Task 2). `FinanceDB.read_snapshot/write_snapshot/list_periods/accruals/fees` (Task 3) match their calls in `snapshots.py`/`mcp_server.py` (Tasks 4-5). `reports.balance_sheet/income_statement/nim/segment_pnl` signatures match their MCP call sites. Snapshot convention (debit−credit) is stated in Global Constraints and consumed consistently.

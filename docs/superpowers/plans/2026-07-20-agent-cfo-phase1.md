# Agent CFO — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Agent CFO Phase 1 — an autonomous, read-only financial-officer agent (GLM-5.2 on Ollama) that reads nano-bank's finance reports, computes CFO metrics (RAROC, ROE/ROA, efficiency ratio, LDR, …), and answers financial-health questions — plus the pure metric tools it consumes.

**Architecture:** The metric math is added to the existing **finance service** (`finance/metrics.py`, pure & unit-tested) and exposed as three new MCP tools on `:8088`. A new self-contained **`cfo/`** subsystem runs a LangGraph `create_react_agent` on GLM-5.2 whose only tools are the finance MCP; it serves a FastAPI `/ask` A2A endpoint (`:8089`) and a Streamlit console (`:8506`). No DB access from the CFO — the finance service stays the single source of financial truth.

**Tech Stack:** Python 3.12, `mcp` (FastMCP, streamable-HTTP), LangGraph / `langchain-mcp-adapters` / `langchain-openai` (OpenAI-compat → Ollama), FastAPI + uvicorn, Streamlit, psycopg2, pytest, Decimal for all money.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-20-agent-cfo-phase1-design.md`. Branch: `agent-cfo`.
- **Read-only over the bank.** The CFO's only tools are the finance MCP. No DB, no core, no bank API from `cfo/`.
- **Tools do the arithmetic.** The LLM never computes a financial figure; every number comes from a tool result.
- **All money is `Decimal`.** Never use float for financial values.
- **Debit−credit snapshot convention:** assets/expenses stored positive, liabilities/equity/income negative (matches existing `finance/reports.py`, `finance/roles.py`).
- **House MCP pattern:** `FastMCP(..., transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`, `@mcp.tool()`, served via `uvicorn.run(mcp.streamable_http_app(), ...)`.
- **GLM model default:** `glm-5.2` via Ollama OpenAI-compat (`OLLAMA_BASE_URL` default `https://ollama.com/v1`), same as `agent/`.
- **Ports:** CFO A2A `:8089`, CFO console `:8506`, finance MCP `:8088` (existing). Do not collide with `:8081/8086/8087/8088/8090/8091/8504/8505`.
- **Env conventions** mirror `finance/config.py` / `agent/config.py`: plain `os.environ` with `Settings.from_env(env=None)`.
- **Run tests** from the repo root `nano-bank/` with the finance venv active: `source finance/.venv/bin/activate`. The CFO shares that venv but it does **not** yet have the LangGraph/FastAPI/Streamlit deps — install them once in Task 5 (`pip install -r cfo/requirements.txt`) before running any `cfo/tests`.
- **In-cluster Ollama secret:** the existing secret is `nano-agent-secrets` (created by `agent/k8s/deploy.sh` from `agent/.env`), holding `OLLAMA_API_KEY`; deployments consume it via `envFrom: - secretRef: { name: nano-agent-secrets }`. The CFO reuses it the same way.

---

## File structure

**Finance service (existing dir `finance/`):**
- Create `finance/metrics.py` — pure metric math (economic capital, expected loss, RAROC, key ratios, financial-health bundle).
- Modify `finance/config.py` — add `RiskConfig` (weights, loss rates, target ratio; defaults + env).
- Modify `finance/mcp_server.py` — expose `raroc`, `key_ratios`, `financial_health` tools.
- Create `finance/tests/test_metrics.py` — unit tests for the math.
- Modify `finance/tests/test_mcp.py` — assert the three new tools register.

**CFO subsystem (new dir `cfo/`):**
- `cfo/__init__.py`, `cfo/config.py`, `cfo/model_factory.py`, `cfo/trace.py`, `cfo/tools.py`, `cfo/agent.py`, `cfo/api_main.py`, `cfo/console.py`
- `cfo/requirements.txt`, `cfo/Dockerfile`, `cfo/k8s/cfo.yaml`, `cfo/README.md`, `cfo/verify-cfo.sh`
- `cfo/tests/__init__.py`, `cfo/tests/test_config.py`, `cfo/tests/test_model_factory.py`, `cfo/tests/test_tools.py`, `cfo/tests/test_agent.py`, `cfo/tests/test_api.py`

---

## Task 1: RiskConfig in the finance service

**Files:**
- Modify: `finance/config.py`
- Test: `finance/tests/test_config.py` (create)

**Interfaces:**
- Produces: `RiskConfig` dataclass with fields `risk_weights: dict[str, Decimal]`, `loss_rates: dict[str, Decimal]`, `target_ratio: Decimal`; classmethods `RiskConfig.default() -> RiskConfig` and `RiskConfig.from_env(env=None) -> RiskConfig`.

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_config.py`:

```python
from decimal import Decimal as D
from finance.config import RiskConfig


def test_default_risk_config_matches_spec():
    rc = RiskConfig.default()
    assert rc.target_ratio == D("0.10")
    assert rc.risk_weights["CardReceivable"] == D("0.75")
    assert rc.risk_weights["TreasuryPlacement"] == D("0.20")
    assert rc.risk_weights["OverdraftReceivable"] == D("1.00")
    assert rc.risk_weights["LoansReceivable"] == D("1.00")
    assert rc.risk_weights["CashReserves"] == D("0")
    assert rc.loss_rates["CardReceivable"] == D("0.03")
    assert rc.loss_rates["OverdraftReceivable"] == D("0.02")
    assert rc.loss_rates["LoansReceivable"] == D("0.015")


def test_from_env_overrides_target_and_a_weight():
    rc = RiskConfig.from_env({
        "RISK_TARGET_RATIO": "0.12",
        "RISK_WEIGHT_CardReceivable": "0.80",
        "RISK_LOSS_LoansReceivable": "0.02",
    })
    assert rc.target_ratio == D("0.12")
    assert rc.risk_weights["CardReceivable"] == D("0.80")
    assert rc.risk_weights["TreasuryPlacement"] == D("0.20")   # untouched default
    assert rc.loss_rates["LoansReceivable"] == D("0.02")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source finance/.venv/bin/activate && python -m pytest finance/tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'RiskConfig'`.

- [ ] **Step 3: Implement RiskConfig**

Add to `finance/config.py` (keep the existing `Settings`; add imports `from dataclasses import dataclass` is already there, add `from decimal import Decimal`):

```python
from decimal import Decimal

_DEFAULT_WEIGHTS = {
    "CashReserves": Decimal("0"),
    "TreasuryPlacement": Decimal("0.20"),
    "CardReceivable": Decimal("0.75"),
    "OverdraftReceivable": Decimal("1.00"),
    "LoansReceivable": Decimal("1.00"),
}
_DEFAULT_LOSS = {
    "CardReceivable": Decimal("0.03"),
    "OverdraftReceivable": Decimal("0.02"),
    "LoansReceivable": Decimal("0.015"),
}


@dataclass(frozen=True)
class RiskConfig:
    """Basel-lite capital model for RAROC (spec #5 replaces this behind raroc())."""
    risk_weights: dict
    loss_rates: dict
    target_ratio: Decimal

    @classmethod
    def default(cls) -> "RiskConfig":
        return cls(risk_weights=dict(_DEFAULT_WEIGHTS),
                   loss_rates=dict(_DEFAULT_LOSS),
                   target_ratio=Decimal("0.10"))

    @classmethod
    def from_env(cls, env=None) -> "RiskConfig":
        import os
        e = os.environ if env is None else env
        weights = dict(_DEFAULT_WEIGHTS)
        loss = dict(_DEFAULT_LOSS)
        for role in list(weights):
            if (v := e.get(f"RISK_WEIGHT_{role}")) is not None:
                weights[role] = Decimal(v)
        for role in list(loss):
            if (v := e.get(f"RISK_LOSS_{role}")) is not None:
                loss[role] = Decimal(v)
        ratio = Decimal(e.get("RISK_TARGET_RATIO", "0.10"))
        return cls(risk_weights=weights, loss_rates=loss, target_ratio=ratio)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest finance/tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add finance/config.py finance/tests/test_config.py
git commit -m "feat(finance): RiskConfig capital model for RAROC (Basel-lite defaults + env)"
```

---

## Task 2: Economic capital, expected loss, RAROC

**Files:**
- Create: `finance/metrics.py`
- Test: `finance/tests/test_metrics.py` (create)

**Interfaces:**
- Consumes: `RiskConfig` (Task 1); `finance.reports.income_statement`; `finance.roles.EARNING_ASSET_ROLES`.
- Produces:
  - `economic_capital(snapshot: dict, risk: RiskConfig) -> dict` with keys `rwa` (dict role→Decimal), `total_rwa: Decimal`, `economic_capital: Decimal`.
  - `expected_loss(snapshot: dict, risk: RiskConfig) -> Decimal`.
  - `raroc(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict` with keys `net_income`, `net_income_annualized`, `expected_loss`, `risk_adjusted_return`, `economic_capital`, `total_rwa`, `rwa`, `raroc`.

- [ ] **Step 1: Write the failing test**

Create `finance/tests/test_metrics.py`:

```python
from decimal import Decimal as D
from finance import metrics
from finance.config import RiskConfig

RC = RiskConfig.default()


def _assets():
    # closing balances, debit-normal (assets +)
    return {
        "CardReceivable": D("10000"),
        "OverdraftReceivable": D("4000"),
        "LoansReceivable": D("6000"),
        "TreasuryPlacement": D("5000"),
        "CashReserves": D("2000"),
    }


def test_economic_capital_rwa_and_ec():
    ec = metrics.economic_capital(_assets(), RC)
    # RWA: card .75*10000=7500, od 1*4000=4000, loan 1*6000=6000,
    #      treasury .20*5000=1000, cash 0*2000=0  -> 18500
    assert ec["total_rwa"] == D("18500.00")
    assert ec["rwa"]["CardReceivable"] == D("7500.00")
    assert ec["economic_capital"] == D("18500.00") * D("0.10")


def test_expected_loss():
    el = metrics.expected_loss(_assets(), RC)
    # .03*10000 + .02*4000 + .015*6000 = 300 + 80 + 90 = 470
    assert el == D("300.00") + D("80.00") + D("90.000")


def test_raroc_components():
    closing = dict(_assets(),
                   InterestIncome=D("-1000"), InterestExpense=D("200"),
                   OperatingExpense=D("100"), FeeIncome=D("-50"))
    opening = {"InterestIncome": D("0"), "InterestExpense": D("0"),
               "OperatingExpense": D("0"), "FeeIncome": D("0")}
    out = metrics.raroc(closing, opening, days=30, risk=RC)
    # income statement net income: income (1000+50) - expense (200+100) = 750
    assert out["net_income"] == D("750")
    assert out["net_income_annualized"] == D("750") * D("365") / D("30")
    assert out["expected_loss"] == D("470.000")
    assert out["economic_capital"] == D("18500.00") * D("0.10")
    assert out["risk_adjusted_return"] == (
        out["net_income_annualized"] - out["expected_loss"])
    assert out["raroc"] == out["risk_adjusted_return"] / out["economic_capital"]


def test_raroc_zero_capital_is_safe():
    out = metrics.raroc({}, {}, days=30, risk=RC)
    assert out["economic_capital"] == D("0.00")
    assert out["raroc"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest finance/tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finance.metrics'`.

- [ ] **Step 3: Implement the module**

Create `finance/metrics.py`:

```python
"""Pure CFO-metric math over period snapshots (debit-credit convention).
No DB/IO — every function is unit-testable in isolation. Money is Decimal.
The RAROC capital model is Basel-lite (finance.config.RiskConfig); spec #5
replaces it behind the same signatures.
"""
from __future__ import annotations
from decimal import Decimal
from . import reports, roles
from .config import RiskConfig


def _safe_div(n: Decimal, d: Decimal):
    return n / d if d else None


def economic_capital(snapshot: dict, risk: RiskConfig) -> dict:
    rwa: dict[str, Decimal] = {}
    for role, weight in risk.risk_weights.items():
        bal = snapshot.get(role, Decimal(0))
        rwa[role] = (bal * weight).quantize(Decimal("0.01"))
    total = sum(rwa.values(), Decimal(0))
    return {"rwa": rwa, "total_rwa": total,
            "economic_capital": (total * risk.target_ratio).quantize(Decimal("0.01"))}


def expected_loss(snapshot: dict, risk: RiskConfig) -> Decimal:
    total = Decimal(0)
    for role, rate in risk.loss_rates.items():
        total += snapshot.get(role, Decimal(0)) * rate
    return total


def raroc(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict:
    inc = reports.income_statement(closing, opening)
    factor = Decimal(365) / Decimal(days)
    ni = inc["net_income"]
    ni_ann = ni * factor
    el = expected_loss(closing, risk)
    ec = economic_capital(closing, risk)
    rar = ni_ann - el
    return {
        "net_income": ni,
        "net_income_annualized": ni_ann,
        "expected_loss": el,
        "risk_adjusted_return": rar,
        "economic_capital": ec["economic_capital"],
        "total_rwa": ec["total_rwa"],
        "rwa": ec["rwa"],
        "raroc": _safe_div(rar, ec["economic_capital"]),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest finance/tests/test_metrics.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add finance/metrics.py finance/tests/test_metrics.py
git commit -m "feat(finance): economic capital, expected loss, RAROC (Basel-lite)"
```

---

## Task 3: Key ratios

**Files:**
- Modify: `finance/metrics.py`
- Test: `finance/tests/test_metrics.py`

**Interfaces:**
- Consumes: `economic_capital` (Task 2); `finance.reports.balance_sheet`, `income_statement`, `nim`.
- Produces: `key_ratios(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict` with keys `roa`, `roe`, `efficiency_ratio`, `loan_to_deposit`, `leverage_ratio`, `rwa_capital_ratio`, `cost_of_funds`, `yield_on_earning_assets` (each a Decimal or `None` when the denominator is zero).

- [ ] **Step 1: Write the failing test**

Append to `finance/tests/test_metrics.py`:

```python
def test_key_ratios():
    closing = {
        "CashReserves": D("5000"), "CardReceivable": D("10000"),
        "TreasuryPlacement": D("5000"),
        "CustomerDeposits": D("-16000"),          # deposits 16000
        "Capital": D("-3000"),                    # equity 3000 (ex earnings)
        "InterestIncome": D("-1000"), "InterestExpense": D("200"),
        "OperatingExpense": D("100"), "FeeIncome": D("-50"),
    }
    opening = {
        "CardReceivable": D("10000"), "TreasuryPlacement": D("5000"),
        "CustomerDeposits": D("-16000"),
        "InterestIncome": D("0"), "InterestExpense": D("0"),
        "OperatingExpense": D("0"), "FeeIncome": D("0"),
    }
    r = metrics.key_ratios(closing, opening, days=30, risk=RC)
    factor = D("365") / D("30")
    # net income = income(1050) - expense(300) = 750; annualised = 750*factor
    ni_ann = D("750") * factor
    # total assets = 5000+10000+5000 = 20000
    assert r["roa"] == ni_ann / D("20000")
    # capital base = equity excluding CurrentEarnings = 3000
    assert r["roe"] == ni_ann / D("3000")
    # efficiency = opex(100) / total_revenue(net interest 800 + fee 50) = 100/850
    assert r["efficiency_ratio"] == D("100") / D("850")
    # LDR = loans(10000) / deposits(16000)
    assert r["loan_to_deposit"] == D("10000") / D("16000")
    # cost of funds = interest_expense annualised / avg deposits(16000)
    assert r["cost_of_funds"] == (D("200") * factor) / D("16000")


def test_key_ratios_guard_zero_denominators():
    r = metrics.key_ratios({}, {}, days=30, risk=RC)
    assert r["roa"] is None
    assert r["loan_to_deposit"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest finance/tests/test_metrics.py::test_key_ratios -v`
Expected: FAIL with `AttributeError: module 'finance.metrics' has no attribute 'key_ratios'`.

- [ ] **Step 3: Implement key_ratios**

Append to `finance/metrics.py`:

```python
def key_ratios(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict:
    bs = reports.balance_sheet(closing)
    inc = reports.income_statement(closing, opening)
    nim_out = reports.nim(closing, opening, days)
    ec = economic_capital(closing, risk)
    factor = Decimal(365) / Decimal(days)
    ni_ann = inc["net_income"] * factor

    ii = inc["income"].get("InterestIncome", Decimal(0))
    ie = inc["expense"].get("InterestExpense", Decimal(0))
    fee = inc["income"].get("FeeIncome", Decimal(0))
    interchange = inc["income"].get("InterchangeIncome", Decimal(0))
    opex = inc["expense"].get("OperatingExpense", Decimal(0))
    total_revenue = (ii - ie) + fee + interchange

    total_assets = bs["total_assets"]
    total_equity = sum(bs["equity"].values(), Decimal(0))
    capital_base = sum((v for k, v in bs["equity"].items()
                        if k != "CurrentEarnings"), Decimal(0))
    loans = sum((closing.get(r, Decimal(0)) for r in
                 ("CardReceivable", "OverdraftReceivable", "LoansReceivable")),
                Decimal(0))
    deposits_close = -closing.get("CustomerDeposits", Decimal(0))
    deposits_open = -opening.get("CustomerDeposits", Decimal(0))
    avg_deposits = (deposits_open + deposits_close) / Decimal(2)

    return {
        "roa": _safe_div(ni_ann, total_assets),
        "roe": _safe_div(ni_ann, capital_base),
        "efficiency_ratio": _safe_div(opex, total_revenue),
        "loan_to_deposit": _safe_div(loans, deposits_close),
        "leverage_ratio": _safe_div(total_equity, total_assets),
        "rwa_capital_ratio": _safe_div(total_equity, ec["total_rwa"]),
        "cost_of_funds": _safe_div(ie * factor, avg_deposits),
        "yield_on_earning_assets": _safe_div(ii * factor,
                                             nim_out["avg_earning_assets"]),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest finance/tests/test_metrics.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add finance/metrics.py finance/tests/test_metrics.py
git commit -m "feat(finance): key CFO ratios (ROA/ROE/efficiency/LDR/leverage/CoF/yield)"
```

---

## Task 4: financial_health bundle + three MCP tools

**Files:**
- Modify: `finance/metrics.py`
- Modify: `finance/mcp_server.py`
- Test: `finance/tests/test_metrics.py`, `finance/tests/test_mcp.py`

**Interfaces:**
- Consumes: `raroc`, `key_ratios` (Tasks 2–3); the MCP `_month_range`, `_stringify`, `Deps`, `deps.db.read_snapshot` (existing `finance/mcp_server.py`).
- Produces:
  - `financial_health(closing, opening, days, risk) -> dict` with keys `balance_sheet`, `income_statement`, `nim`, `key_ratios`, `raroc`.
  - MCP tools `raroc(period)`, `key_ratios(period)`, `financial_health(period)` on the finance server.

- [ ] **Step 1: Write the failing tests**

Append to `finance/tests/test_metrics.py`:

```python
def test_financial_health_bundle_keys():
    closing = {"CashReserves": D("100"), "Capital": D("-100"),
               "InterestIncome": D("-10")}
    opening = {"InterestIncome": D("0")}
    fh = metrics.financial_health(closing, opening, days=30, risk=RC)
    assert set(fh) == {"balance_sheet", "income_statement", "nim",
                       "key_ratios", "raroc"}
    assert fh["raroc"]["net_income"] == D("10")
```

Append to `finance/tests/test_mcp.py` (read the file first to match its fixtures; add a test that builds the MCP and lists tool names):

```python
import asyncio


def test_new_metric_tools_registered():
    from finance.mcp_server import build_mcp, Deps

    class _DB:
        def read_snapshot(self, period): return {}
        def list_periods(self): return []
    mcp = build_mcp(Deps(db=_DB(), nano_bank_api="http://x"))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"raroc", "key_ratios", "financial_health"} <= names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest finance/tests/test_metrics.py::test_financial_health_bundle_keys finance/tests/test_mcp.py::test_new_metric_tools_registered -v`
Expected: FAIL (`financial_health` missing; tools not registered).

- [ ] **Step 3a: Implement financial_health**

Append to `finance/metrics.py`:

```python
def financial_health(closing: dict, opening: dict, days: int,
                     risk: RiskConfig) -> dict:
    return {
        "balance_sheet": reports.balance_sheet(closing),
        "income_statement": reports.income_statement(closing, opening),
        "nim": reports.nim(closing, opening, days),
        "key_ratios": key_ratios(closing, opening, days, risk),
        "raroc": raroc(closing, opening, days, risk),
    }
```

- [ ] **Step 3b: Wire the MCP tools**

In `finance/mcp_server.py`, add imports at top: `from . import metrics` and `from .config import RiskConfig`. Register three tools inside `build_mcp` (after `segment_pnl`), reusing the existing `_month_range` / `_stringify` / `deps.db.read_snapshot`:

```python
    @mcp.tool()
    def raroc(period: str) -> dict:
        """Risk-adjusted return on capital (Basel-lite) for a period."""
        _, _, prior, days = _month_range(period)
        return _stringify(metrics.raroc(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior),
            days, RiskConfig.from_env()))

    @mcp.tool()
    def key_ratios(period: str) -> dict:
        """Key CFO ratios (ROA/ROE/efficiency/LDR/leverage/CoF/yield) for a period."""
        _, _, prior, days = _month_range(period)
        return _stringify(metrics.key_ratios(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior),
            days, RiskConfig.from_env()))

    @mcp.tool()
    def financial_health(period: str) -> dict:
        """Full financial-health bundle: balance sheet, income statement, NIM,
        key ratios and RAROC for a period."""
        _, _, prior, days = _month_range(period)
        return _stringify(metrics.financial_health(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior),
            days, RiskConfig.from_env()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest finance/tests/ -v`
Expected: PASS (all finance tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add finance/metrics.py finance/mcp_server.py finance/tests/test_metrics.py finance/tests/test_mcp.py
git commit -m "feat(finance): financial_health bundle + raroc/key_ratios/financial_health MCP tools"
```

---

## Task 5: CFO subsystem scaffold + config

**Files:**
- Create: `cfo/__init__.py`, `cfo/config.py`, `cfo/requirements.txt`, `cfo/tests/__init__.py`, `cfo/tests/test_config.py`

**Interfaces:**
- Produces: `cfo.config.Settings` dataclass with fields `ollama_api_key`, `ollama_base_url`, `cfo_model`, `finance_mcp_url`, `api_port`, `console_port`; classmethod `Settings.from_env(env=None) -> Settings`.

- [ ] **Step 1: Write the failing test**

Create `cfo/__init__.py` (empty) and `cfo/tests/__init__.py` (empty), then `cfo/tests/test_config.py`:

```python
from cfo.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.cfo_model == "glm-5.2"
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.finance_mcp_url == "http://localhost:8088/mcp"
    assert s.api_port == 8089
    assert s.console_port == 8506


def test_env_overrides():
    s = Settings.from_env({"CFO_MODEL": "glm-5.2-air", "API_PORT": "9000",
                           "FINANCE_MCP_URL": "http://finance-mcp:8088/mcp"})
    assert s.cfo_model == "glm-5.2-air"
    assert s.api_port == 9000
    assert s.finance_mcp_url == "http://finance-mcp:8088/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cfo/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cfo.config'`.

- [ ] **Step 3: Implement config + requirements**

Create `cfo/config.py`:

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    cfo_model: str
    finance_mcp_url: str
    api_port: int
    console_port: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            cfo_model=g("CFO_MODEL", "glm-5.2"),
            finance_mcp_url=g("FINANCE_MCP_URL", "http://localhost:8088/mcp"),
            api_port=int(g("API_PORT", "8089")),
            console_port=int(g("CONSOLE_PORT", "8506")),
        )
```

Create `cfo/requirements.txt`:

```
mcp>=1.2
langgraph>=0.2
langchain-core>=0.3
langchain-openai>=0.2
langchain-mcp-adapters>=0.1
fastapi>=0.115
uvicorn>=0.30
streamlit>=1.38
httpx>=0.27
pytest>=8.0
```

- [ ] **Step 4: Install deps into the shared venv, then run the test**

The finance venv lacks LangGraph/FastAPI/Streamlit; install the CFO deps once:
Run: `source finance/.venv/bin/activate && pip install -r cfo/requirements.txt`
Then: `python -m pytest cfo/tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/__init__.py cfo/config.py cfo/requirements.txt cfo/tests/__init__.py cfo/tests/test_config.py
git commit -m "feat(cfo): subsystem scaffold + config"
```

---

## Task 6: CFO model factory

**Files:**
- Create: `cfo/model_factory.py`, `cfo/tests/test_model_factory.py`

**Interfaces:**
- Consumes: `cfo.config.Settings`.
- Produces: `build_model(model, settings, *, temperature=0.1, max_tokens=None) -> ChatOpenAI`; `resolve_model(settings, probe=None) -> str`; `init_models(settings, probe=None) -> str`; `llm(*, temperature=0.1, max_tokens=None) -> ChatOpenAI`; `backend_healthcheck(settings) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_model_factory.py`:

```python
import pytest
from cfo.config import Settings
from cfo import model_factory as mf


def _settings():
    return Settings.from_env({"OLLAMA_API_KEY": "x"})


def test_resolver_picks_model_when_it_probes():
    s = _settings()
    assert mf.resolve_model(s, probe=lambda model, st: model == "glm-5.2") == "glm-5.2"


def test_resolver_raises_when_probe_fails():
    with pytest.raises(RuntimeError):
        mf.resolve_model(_settings(), probe=lambda model, st: False)


def test_llm_requires_init(monkeypatch):
    monkeypatch.setattr(mf, "_RESOLVED", None, raising=False)
    with pytest.raises(RuntimeError):
        mf.llm()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cfo/tests/test_model_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cfo.model_factory'`.

- [ ] **Step 3: Implement model_factory**

Create `cfo/model_factory.py` (mirrors `agent/model_factory.py`, single model = `settings.cfo_model`, default temperature 0.1 for numeric discipline):

```python
from __future__ import annotations
import logging
from functools import lru_cache
from typing import Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from .config import Settings

log = logging.getLogger("cfo.llm")

_RESOLVED: Optional[str] = None
_SETTINGS: Optional[Settings] = None


def build_model(model: str, settings: Settings, *, temperature: float = 0.1,
                max_tokens: Optional[int] = None) -> ChatOpenAI:
    kw = dict(model=model, temperature=temperature, base_url=settings.ollama_base_url,
              api_key=settings.ollama_api_key or "ollama", timeout=600)
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return ChatOpenAI(**kw)


def _default_probe(model: str, settings: Settings) -> bool:
    try:
        m = build_model(model, settings, temperature=0.0, max_tokens=8)
        m.invoke([HumanMessage("reply with the single word: ok")])
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("probe failed for %s: %s", model, e)
        return False


def resolve_model(settings: Settings, probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    probe = probe or _default_probe
    model = settings.cfo_model
    if probe(model, settings):
        log.info("resolved model: %s", model)
        return model
    raise RuntimeError(f"{model} did not answer at {settings.ollama_base_url}")


def init_models(settings: Settings, probe: Optional[Callable[[str, Settings], bool]] = None) -> str:
    global _RESOLVED, _SETTINGS
    _SETTINGS = settings
    _RESOLVED = resolve_model(settings, probe)
    return _RESOLVED


@lru_cache(maxsize=8)
def _client(model: str, temperature: float, max_tokens: Optional[int]) -> ChatOpenAI:
    return build_model(model, _SETTINGS, temperature=temperature, max_tokens=max_tokens)


def llm(*, temperature: float = 0.1, max_tokens: Optional[int] = None) -> ChatOpenAI:
    if _RESOLVED is None or _SETTINGS is None:
        raise RuntimeError("call init_models(settings) before llm()")
    return _client(_RESOLVED, temperature, max_tokens)


def backend_healthcheck(settings: Settings) -> bool:
    try:
        return _default_probe(settings.cfo_model, settings)
    except Exception:  # noqa: BLE001
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cfo/tests/test_model_factory.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/model_factory.py cfo/tests/test_model_factory.py
git commit -m "feat(cfo): GLM-5.2 model factory (probe + resolve + cached client)"
```

---

## Task 7: MCP tool wiring + trace recorder

**Files:**
- Create: `cfo/trace.py`, `cfo/tools.py`, `cfo/tests/test_tools.py`

**Interfaces:**
- Consumes: `cfo.config.Settings`; `langchain_mcp_adapters.client.MultiServerMCPClient`.
- Produces:
  - `cfo.trace.TraceRecorder` (a `langchain_core.callbacks.BaseCallbackHandler` with `.events() -> list[dict]`).
  - `cfo.tools.mcp_client(settings) -> MultiServerMCPClient` (bound to the finance MCP, no per-customer headers).
  - `cfo.tools.get_tools(settings) -> list` (async) — returns the finance MCP tools.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_tools.py`:

```python
from cfo.config import Settings
from cfo import tools


def test_mcp_client_targets_finance_mcp():
    s = Settings.from_env({"FINANCE_MCP_URL": "http://finance-mcp:8088/mcp"})
    client = tools.mcp_client(s)
    conns = client.connections
    assert conns["finance"]["url"] == "http://finance-mcp:8088/mcp"
    assert conns["finance"]["transport"] == "streamable_http"
    # bank-wide: no per-customer headers
    assert "headers" not in conns["finance"] or conns["finance"]["headers"] == {}


def test_trace_recorder_records_tool_events():
    from cfo.trace import TraceRecorder
    rec = TraceRecorder()
    rec.on_tool_start({"name": "raroc"}, "2026-07", run_id="r1")
    rec.on_tool_end("{...}", run_id="r1")
    ev = rec.events()
    assert ev and ev[0]["name"] == "raroc" and ev[0]["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cfo/tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cfo.tools'`.

- [ ] **Step 3: Implement trace + tools**

Create `cfo/trace.py` — copy the recorder verbatim from `agent/trace.py` (same class; it is generic and has no agent coupling).

Create `cfo/tools.py`:

```python
from __future__ import annotations
from .config import Settings


def mcp_client(settings: Settings):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    return MultiServerMCPClient({
        "finance": {
            "url": settings.finance_mcp_url,
            "transport": "streamable_http",
        }
    })


async def get_tools(settings: Settings) -> list:
    return await mcp_client(settings).get_tools()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cfo/tests/test_tools.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/trace.py cfo/tools.py cfo/tests/test_tools.py
git commit -m "feat(cfo): finance MCP tool wiring + trace recorder"
```

---

## Task 8: The CFO agent (prompt + ask)

**Files:**
- Create: `cfo/agent.py`, `cfo/tests/test_agent.py`

**Interfaces:**
- Consumes: `cfo.config.Settings`; `cfo.model_factory.llm`; `cfo.tools.get_tools`; `cfo.trace.TraceRecorder`; `langgraph.prebuilt.create_react_agent`; `langgraph.checkpoint.memory.InMemorySaver`.
- Produces: `CFO_PROMPT: str`; `async ask(settings, message, thread_id=None) -> dict` returning `{"answer": str, "thread_id": str, "trace": list}`.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_agent.py` (drives `ask` with a fake tool list + a stub agent so no network/LLM is needed):

```python
import asyncio
from unittest.mock import patch
from langchain_core.messages import AIMessage, HumanMessage

from cfo.config import Settings
from cfo import agent as cfo_agent


class _FakeAgent:
    async def ainvoke(self, state, config=None):
        return {"messages": state["messages"] +
                [AIMessage("RAROC is 18.3%, which is healthy.")]}


def test_prompt_pins_discipline():
    p = cfo_agent.CFO_PROMPT.lower()
    assert "chief financial officer" in p
    assert "never" in p and "tool" in p


def test_ask_returns_answer_and_thread():
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})

    async def _fake_get_tools(settings):
        return []

    with patch.object(cfo_agent, "get_tools", _fake_get_tools), \
         patch.object(cfo_agent, "create_react_agent", return_value=_FakeAgent()), \
         patch.object(cfo_agent.mf, "llm", return_value=object()):
        out = asyncio.run(cfo_agent.ask(s, "How healthy are we?", thread_id="t1"))
    assert out["thread_id"] == "t1"
    assert "RAROC" in out["answer"]
    assert isinstance(out["trace"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cfo/tests/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cfo.agent'`.

- [ ] **Step 3: Implement the agent**

Create `cfo/agent.py`:

```python
from __future__ import annotations
import uuid
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

from .config import Settings
from . import model_factory as mf
from .tools import get_tools
from .trace import TraceRecorder

CFO_PROMPT = (
    "You are the Chief Financial Officer of nano-bank; you speak for the whole "
    "bank's finances. Answer ONLY from your finance tools; never fabricate a "
    "figure, rate, or trend. ALWAYS compute metrics by calling the tools "
    "(financial_health, raroc, key_ratios, balance_sheet, income_statement, "
    "nim, segment_pnl) — never do the arithmetic yourself. If a period is not "
    "closed, call list_periods and use an available period or offer to run "
    "close_period; do not guess un-closed figures. When you state a metric, "
    "briefly say what it means and whether it looks healthy, but ground every "
    "number in a tool result. You are an analyst: you may recommend, but you "
    "take no actions — you cannot move money, post entries, or commit budgets."
)


async def ask(settings: Settings, message: str,
              thread_id: Optional[str] = None) -> dict:
    thread_id = thread_id or f"cfo-{uuid.uuid4().hex[:6]}"
    tools = await get_tools(settings)
    rec = TraceRecorder()
    agent = create_react_agent(mf.llm(), tools, prompt=CFO_PROMPT,
                               checkpointer=InMemorySaver())
    out = await agent.ainvoke(
        {"messages": [HumanMessage(message)]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 40,
                "callbacks": [rec]})
    answer = "(no answer)"
    for m in reversed(out["messages"]):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            answer = m.content
            break
    return {"answer": answer, "thread_id": thread_id, "trace": rec.events()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cfo/tests/test_agent.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/agent.py cfo/tests/test_agent.py
git commit -m "feat(cfo): CFO react agent + prompt + ask() contract"
```

---

## Task 9: FastAPI A2A endpoint

**Files:**
- Create: `cfo/api.py`, `cfo/api_main.py`, `cfo/tests/test_api.py`

**Interfaces:**
- Consumes: `cfo.config.Settings`; `cfo.agent.ask`; `cfo.model_factory` (`init_models`, `backend_healthcheck`); FastAPI `TestClient`.
- Produces: `cfo.api.create_app(settings, ask_fn=None) -> FastAPI` with `POST /ask` (`{message, thread_id?}` → `{answer, thread_id, trace}`) and `GET /health`; `cfo.api_main.build() -> (Settings, FastAPI)`.

- [ ] **Step 1: Write the failing test**

Create `cfo/tests/test_api.py`:

```python
from fastapi.testclient import TestClient
from cfo.config import Settings
from cfo.api import create_app


def _client(ask_fn):
    s = Settings.from_env({"OLLAMA_API_KEY": "x"})
    return TestClient(create_app(s, ask_fn=ask_fn))


def test_ask_endpoint_returns_answer():
    async def fake_ask(settings, message, thread_id=None):
        return {"answer": f"echo:{message}", "thread_id": thread_id or "t",
                "trace": []}
    r = _client(fake_ask).post("/ask", json={"message": "hi", "thread_id": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "echo:hi"
    assert body["thread_id"] == "t1"


def test_health_endpoint():
    async def fake_ask(*a, **k):
        return {"answer": "", "thread_id": "t", "trace": []}
    r = _client(fake_ask).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest cfo/tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cfo.api'`.

- [ ] **Step 3: Implement the app + entrypoint**

Create `cfo/api.py`:

```python
from __future__ import annotations
from typing import Callable, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Settings
from .agent import ask as default_ask


class AskRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


def create_app(settings: Settings, ask_fn: Optional[Callable] = None) -> FastAPI:
    ask_fn = ask_fn or default_ask
    app = FastAPI(title="nano-bank CFO")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "cfo"}

    @app.post("/ask")
    async def ask_endpoint(req: AskRequest):
        return await ask_fn(settings, req.message, req.thread_id)

    return app
```

Create `cfo/api_main.py`:

```python
"""Container entrypoint for the CFO A2A API: resolve GLM at startup, serve."""
from __future__ import annotations
import uvicorn

from .config import Settings
from . import model_factory as mf
from .api import create_app


def build():
    settings = Settings.from_env()
    mf.init_models(settings)
    return settings, create_app(settings)


if __name__ == "__main__":
    settings, app = build()
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest cfo/tests/test_api.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add cfo/api.py cfo/api_main.py cfo/tests/test_api.py
git commit -m "feat(cfo): FastAPI /ask A2A endpoint + /health + entrypoint"
```

---

## Task 10: Console + Dockerfile + k8s + README

**Files:**
- Create: `cfo/console.py`, `cfo/Dockerfile`, `cfo/k8s/cfo.yaml`, `cfo/README.md`

**Interfaces:**
- Consumes: `cfo.config.Settings`; `cfo.api_main` (container CMD); the FastAPI `/ask` endpoint (console posts to it).
- Produces: deployable container + manifest + human console. (No unit test — this task is UI/glue; it is verified live in Task 11.)

- [ ] **Step 1: Streamlit console**

Create `cfo/console.py` (a thin chat client that POSTs to the CFO `/ask` endpoint so the console never needs the LLM/MCP itself):

```python
"""Streamlit chat console for the Agent CFO. Talks to the CFO /ask endpoint."""
from __future__ import annotations
import os
import httpx
import streamlit as st

API = os.environ.get("CFO_API_URL", "http://localhost:8089")

st.set_page_config(page_title="nano-bank CFO", page_icon="📊")
st.title("nano-bank — Agent CFO")

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "history" not in st.session_state:
    st.session_state.history = []

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

if prompt := st.chat_input("Ask the CFO about the bank's finances…"):
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            r = httpx.post(f"{API}/ask",
                           json={"message": prompt,
                                 "thread_id": st.session_state.thread_id},
                           timeout=600)
            r.raise_for_status()
            data = r.json()
            st.session_state.thread_id = data.get("thread_id")
            answer = data.get("answer", "(no answer)")
        except Exception as e:  # noqa: BLE001
            answer = f"⚠️ CFO unreachable: {e}"
        st.markdown(answer)
        st.session_state.history.append(("assistant", answer))
```

- [ ] **Step 2: Dockerfile**

Create `cfo/Dockerfile` (mirrors `finance/Dockerfile`; package copied to `/app/cfo`):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/cfo
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "cfo.api_main"]
```

- [ ] **Step 3: k8s manifest**

Create `cfo/k8s/cfo.yaml` (mirrors `finance/k8s/finance-mcp.yaml`; the CFO reaches the in-cluster finance MCP service and needs the Ollama key from the existing secret):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cfo
  namespace: nano-bank
  labels: { app: cfo }
spec:
  replicas: 1
  selector: { matchLabels: { app: cfo } }
  template:
    metadata: { labels: { app: cfo } }
    spec:
      containers:
      - name: cfo
        image: nano-cfo:dev
        imagePullPolicy: Never
        ports: [ { containerPort: 8089 } ]
        envFrom:
        - secretRef: { name: nano-agent-secrets }   # provides OLLAMA_API_KEY
        env:
        - { name: FINANCE_MCP_URL, value: http://finance-mcp:8088/mcp }
        - { name: OLLAMA_BASE_URL, value: https://ollama.com/v1 }
        - { name: CFO_MODEL,       value: glm-5.2 }
        - { name: API_PORT,        value: "8089" }
---
apiVersion: v1
kind: Service
metadata:
  name: cfo
  namespace: nano-bank
spec:
  selector: { app: cfo }
  ports: [ { port: 8089, targetPort: 8089 } ]
```

> Note: `nano-agent-secrets` is created by `agent/k8s/deploy.sh` from `agent/.env`; it must already exist in the `nano-bank` namespace (deploy the agent first, or mint it: `kubectl -n nano-bank create secret generic nano-agent-secrets --from-literal=OLLAMA_API_KEY=…`). It also carries `BRANCH_SERVICE_TOKEN`, which the CFO ignores.

- [ ] **Step 4: README**

Create `cfo/README.md` documenting: what the CFO is (Phase 1 analyst, read-only), how to run locally (finance MCP on :8088 must be up; `OLLAMA_API_KEY=… python -m cfo.api_main`; console `streamlit run cfo/console.py --server.port 8506`), the `/ask` + `/health` endpoints, env vars (`CFO_MODEL`, `FINANCE_MCP_URL`, `API_PORT`, `CONSOLE_PORT`, `RISK_*` on the finance side), and the phase roadmap (Phase 2 actions, Phase 3 meetings, spec #5 real economic capital). Keep it to ~40 lines, matching the tone of `finance/` / `agent/` READMEs.

- [ ] **Step 5: Commit**

```bash
git add cfo/console.py cfo/Dockerfile cfo/k8s/cfo.yaml cfo/README.md
git commit -m "feat(cfo): Streamlit console + Dockerfile + k8s manifest + README"
```

---

## Task 11: Cross-backend live smoke

**Files:**
- Create: `cfo/verify-cfo.sh`

**Interfaces:**
- Consumes: a running core (`CORE_BACKEND=modern|legacy`), the bank API (:8081), the finance MCP (:8088), and the CFO API (:8089).
- Produces: an executable smoke script asserting the CFO returns real figures end-to-end.

- [ ] **Step 1: Write the smoke script**

Create `cfo/verify-cfo.sh` (executable). It assumes the stack is already up (like `finance/verify-reports.sh`); it closes the current period via the finance MCP path used by the existing verify script, then asks the CFO a health question and asserts a numeric answer:

```bash
#!/usr/bin/env bash
set -euo pipefail
# End-to-end CFO smoke. Prereqs (start these first, once per CORE_BACKEND):
#   - a core (modern :8091 or legacy :8090)
#   - bank API :8081  (CORE_BACKEND set accordingly)
#   - finance MCP :8088   (python -m finance.mcp_server)
#   - CFO API :8089       (OLLAMA_API_KEY=… python -m cfo.api_main)
CFO="${CFO_API_URL:-http://localhost:8089}"
PERIOD="${PERIOD:-$(date +%Y-%m)}"

echo "== CFO health =="
curl -fsS "$CFO/health" | tee /dev/stderr | grep -q '"status":"ok"'

echo "== ask the CFO for financial health ($PERIOD) =="
ANSWER=$(curl -fsS -XPOST "$CFO/ask" -H 'content-type: application/json' \
  -d "{\"message\":\"Close period $PERIOD if needed, then tell me our RAROC, ROE and overall financial health with the numbers.\"}" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["answer"])')

echo "$ANSWER"
# The answer must contain at least one figure (digit); pure prose = fail.
echo "$ANSWER" | grep -Eq '[0-9]' || { echo "FAIL: no figures in CFO answer"; exit 1; }
echo "CFO SMOKE PASSED"
```

- [ ] **Step 2: Make it executable + run the full suite offline**

Run:
```bash
chmod +x cfo/verify-cfo.sh
python -m pytest finance/tests/ cfo/tests/ -q
```
Expected: all unit tests pass (finance + cfo). The live smoke itself is run manually once per `CORE_BACKEND` when a stack is up (it needs Ollama + a core).

- [ ] **Step 3: Live smoke, modern core (manual, needs Ollama)**

Bring up modern core + bank (`CORE_BACKEND=modern`) + finance MCP + CFO API, then:
Run: `bash cfo/verify-cfo.sh`
Expected: `CFO SMOKE PASSED`, with an answer containing RAROC/ROE figures.

- [ ] **Step 4: Live smoke, legacy core (manual)**

Repeat with `CORE_BACKEND=legacy` (legacy core :8090). Tear the stack down afterward (kill app/core/finance/cfo by PID; `docker compose down` for the modern core DB), leaving the Kind Postgres `::1:5432` port-forward intact.

- [ ] **Step 5: Commit**

```bash
git add cfo/verify-cfo.sh
git commit -m "test(cfo): cross-backend live smoke (health + grounded figures)"
```

---

## Self-review notes (addressed)

- **Spec coverage:** req 1 (GLM-5.2) → Tasks 6/9; req 2 (reports + RAROC) → Tasks 2–4 (metrics) + 7–8 (agent consumes them); req 5 (financial-health Q&A) → Tasks 8–9 + `financial_health` tool (Task 4). Reqs 3 (meetings) and 4 (actions) are out of Phase-1 scope; the `/ask` endpoint (Task 9) is the meeting seam and the read-only tool set is the seam for Phase-2 write tools — matches the spec's "seams built, not built".
- **Ports** consistent everywhere: CFO API 8089, console 8506, finance MCP 8088.
- **Type consistency:** `ask()` returns `{answer, thread_id, trace}` in Tasks 8/9/API tests; `raroc`/`key_ratios`/`financial_health` signatures identical across metrics module (Tasks 2–4) and MCP wiring (Task 4); `RiskConfig` fields identical across Tasks 1–4.
- **Deployment detail (Task 10):** the CFO reuses the existing `nano-agent-secrets` secret (holds `OLLAMA_API_KEY`) via `envFrom: secretRef` — verified against `agent/k8s/`. Not a code interface; does not block the unit-tested build.

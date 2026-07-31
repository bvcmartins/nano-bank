# Agent CFO — Phase 1 (Analyst / Advisor) — Design

**Date:** 2026-07-20
**Status:** approved (design)
**Depends on:** finance reporting service (`finance/`, MCP `:8088`; branch `finance-reporting-service` / PR #31)
**Repo:** `nano-bank` (new subsystem `cfo/`)

## Purpose

nano-bank is being built to run itself. The **Agent CFO** is the first C-suite
agent: an autonomous financial officer that reads the bank's books, computes the
metrics a CFO cares about (RAROC, ROE/ROA, efficiency ratio, NIM, …), and
answers questions about the bank's financial health. It consumes the finance
reporting service directly and reasons over it — no human dashboard; the agent
is the consumer.

This spec covers **Phase 1 only: an analyst/advisor**. It is deliberately
**read-only over the bank** — it analyses and recommends, it does not move money
or commit budgets. Later phases add actions and multi-agent meetings (see
*Out of scope / later phases*).

## Requirements traceability

The user's five requirements map to phases as:

| # | Requirement | Phase |
|---|-------------|-------|
| 1 | Runs on Ollama GLM-5.2 | **1** |
| 2 | Access to all financial reports + compute metrics like RAROC | **1** |
| 5 | Answer questions about the bank's financial health | **1** |
| 4 | Budget planning & capital allocation (**actions**) | 2 |
| 3 | Interact with CEO/COO/CTO in meetings | 3 |

Phase 1 builds requirements 1, 2, 5 and leaves clean seams for 3 and 4.

## Architecture

A new self-contained subsystem **`cfo/`**, a peer to `agent/` and `finance/`.
Python; a LangGraph `create_react_agent` running on **GLM-5.2 via Ollama**
(OpenAI-compatible endpoint), reusing the `model_factory` / `config` / `trace`
patterns from `agent/` but with its own thin copies so the subsystem is
self-contained.

```
future CEO/COO/CTO agents ─┐
you (Streamlit console) ────┤
                            ▼
        CFO A2A endpoint  POST /ask   (FastAPI, :8089)
                            │
                 LangGraph react agent (GLM-5.2)
                            │  tools = finance MCP only
                            ▼
        finance MCP  (:8088)  ── reads ──►  finance snapshot store  ◄─ core GL
        (balance_sheet, income_statement, nim, segment_pnl,
         raroc, key_ratios, financial_health, close_period, list_periods)
```

Key boundaries:

- **Bank-wide, not customer-scoped** (unlike the personal manager, which is
  bound to one customer). The CFO reasons about the whole bank.
- **Read-only over the bank.** The CFO's entire tool surface is the finance
  MCP. It never touches the DB, the core, or the bank API directly. The finance
  service remains the single source of financial truth. (`close_period`
  refreshes the finance snapshot store from the GL — a finance-ops read/refresh,
  not a bank money movement — and is allowed.)
- **No durable memory in Phase 1.** Conversational continuity is the LangGraph
  thread checkpointer (`InMemorySaver`); the CFO reads live books on every turn,
  so there is nothing to persist yet (YAGNI). Durable memory is a later-phase
  concern.

### Ports

| Service | Port | Notes |
|---------|------|-------|
| CFO A2A endpoint | `:8089` | free; `POST /ask`, `GET /health` |
| CFO console (Streamlit) | `:8506` | free (agent console is `:8505`) |
| finance MCP (existing) | `:8088` | the CFO's only tool source |

## Component 1 — new metric tools in the finance service

The metric math lives in the finance service as **pure, unit-tested functions**
(new module `finance/metrics.py`), exposed as MCP tools alongside the existing
six. The CFO calls them and narrates; it never does the arithmetic itself.

All inputs are the period snapshots the finance service already stores (the
debit−credit trial balance keyed by role) plus the existing `balance_sheet` /
`income_statement` outputs. All figures that represent a rate of return are
**annualized** by `× 365 / days_in_period`.

### `raroc(period)` — Basel-lite risk-adjusted return on capital

```
economic_capital = target_ratio × RWA
  RWA            = Σ risk_weight[class] × earning_asset_balance[class]
expected_loss    = Σ loss_rate[product] × exposure[product]        (annual)
risk_adj_return  = net_income × 365/days − expected_loss           (annualized)
raroc            = risk_adj_return / economic_capital              (0 if EC == 0)
```

Returns: `net_income_annualized`, `expected_loss`, `risk_adjusted_return`,
`economic_capital`, `rwa` (per-class breakdown), `raroc`.

**Config defaults** (a new `RiskConfig` in `finance/config.py`, all env-tunable):

| Asset class (role) | Risk weight | Annual loss rate |
|--------------------|-------------|------------------|
| CashReserves       | 0%          | 0%               |
| TreasuryPlacement  | 20%         | 0%               |
| CardReceivable     | 75%         | 3.0%             |
| OverdraftReceivable| 100%        | 2.0%             |
| LoansReceivable    | 100%        | 1.5%             |

`target_ratio` default **10%**. These are the same `EARNING_ASSET_ROLES` the NIM
report already uses. Exposure for expected loss = the closing earning-asset
balance for that role.

### `key_ratios(period)`

Annualized off the snapshot deltas + the balance sheet:

- **ROA** = net_income_annualized / total_assets
- **ROE** = net_income_annualized / capital_base
  (capital_base = equity **excluding** current-period earnings, to avoid
  circularity; documented in the tool output)
- **efficiency_ratio** = operating_expense / total_revenue
  (total_revenue = net interest income + fee income + interchange income)
- **loan_to_deposit** = earning loans (card + overdraft + loans AR) /
  customer deposits
- **leverage_ratio** = total_equity / total_assets
- **rwa_capital_ratio** = total_equity / RWA (CET1-shaped)
- **cost_of_funds** = interest_expense_annualized / avg customer deposits
- **yield_on_earning_assets** = interest_income_annualized / avg earning assets

Every ratio guards a zero denominator (returns `null`/`0` with the numerator
still reported).

### `financial_health(period)`

A convenience bundle so the CFO gets the whole picture in one tool round-trip:
`{balance_sheet, income_statement, nim, key_ratios, raroc}`. Pure composition of
the other functions; no new math.

## Component 2 — the CFO agent (`cfo/`)

```
cfo/
  __init__.py
  config.py         Settings.from_env: ollama_api_key/base_url, cfo_model
                    (default glm-5.2), finance_mcp_url (default
                    http://localhost:8088/mcp), api_port 8089, console_port 8506
  model_factory.py  thin GLM-5.2 client: build_model + probe + lru cache
                    (mirrors agent/model_factory.py)
  tools.py          MultiServerMCPClient → finance MCP; exposes all finance
                    tools to the agent (no customer scoping / headers)
  agent.py          create_react_agent(llm, tools, prompt=CFO_PROMPT,
                    checkpointer=InMemorySaver); async ask(settings, message,
                    thread_id) -> {answer, thread_id, trace}
  api_main.py       FastAPI: POST /ask {message, thread_id?} -> {answer,...};
                    GET /health (probes Ollama + finance MCP)
  console.py        Streamlit chat console
  trace.py          light tool-call recorder returned with each answer
  Dockerfile
  k8s/cfo.yaml      in-cluster deployment (mirrors agent/finance manifests)
  README.md
  requirements.txt
  tests/            unit tests (fake LLM + fake MCP), api test
  verify-cfo.sh     cross-backend live smoke
```

### CFO_PROMPT (discipline)

Pins the behaviour that makes financial answers trustworthy:

- You are the Chief Financial Officer of nano-bank. You speak for the whole bank.
- Answer **only** from the finance tools; **never fabricate** a figure or trend.
- **Always compute metrics via the tools**, never in your head — call `raroc`,
  `key_ratios`, `financial_health`, etc., and report what they return.
- If a period is not closed, call `list_periods` and either use an available
  period or offer to `close_period`; do not guess at un-closed figures.
- When you state a metric, briefly say what it means and whether it looks
  healthy, but ground every number in a tool result.
- You are an analyst in Phase 1: you may recommend, but you take no actions
  (you cannot move money, post entries, or commit budgets).

### `ask()` contract

`ask(settings, message, thread_id?) -> {answer, thread_id, trace}` — mirrors the
personal manager's `assist()` return shape (answer + trace), minus the
customer/pending-action machinery. `trace` is the ordered list of tool calls the
agent made, so every figure in the answer is auditable back to a tool result.

## Data flow

1. A caller (you via the console, or a future C-suite agent) sends
   `POST /ask {message}`.
2. The LangGraph agent (GLM-5.2) plans and calls finance MCP tools.
3. The finance service reads its snapshot store (populated from the core GL by
   `close_period`), computes the requested report/metric, returns figures.
4. The agent narrates a grounded answer and returns `{answer, trace}`.

## Error handling

- **`/health`** probes both Ollama (model reachable) and the finance MCP
  (reachable) and reports each.
- **Finance MCP down** → the tool call fails; the CFO reports it cannot reach
  the books rather than inventing numbers (prompt-enforced + graceful).
- **Period not closed** → finance tools raise / return empty; the CFO falls back
  to `list_periods` and offers `close_period`.
- **Zero denominators** (e.g. no earning assets yet) → metric tools return
  `null`/`0` for the ratio with the numerator still reported; the CFO explains
  the metric isn't meaningful yet.
- **GLM arithmetic** → mitigated structurally by the tools-do-the-math rule; the
  agent transcribes tool outputs, it does not calculate.

## Testing

- **`finance/tests/test_metrics.py`** — pure unit tests: RAROC with known inputs
  (verify EC, RWA breakdown, EL, annualization), each ratio, zero-denominator
  guards, `financial_health` composition.
- **`cfo/tests/`** — fake-LLM + fake-MCP tests for tool wiring and the prompt; a
  FastAPI `/ask` unit test; a `/health` test with both probes stubbed.
- **`cfo/verify-cfo.sh`** — cross-backend live smoke: bring up a core + the bank
  + finance MCP, `close_period`, seed a little activity, then ask the CFO
  "what's our RAROC and overall financial health?" and assert real figures come
  back. Run once per `CORE_BACKEND` (modern, legacy), like the other verify
  scripts.

## Out of scope / later phases (seams built, not built)

- **Phase 2 — budget planning & capital allocation (actions).** Attaches as a
  **separate confirm-gated writable tool surface**; the Phase-1 read-only tool
  set is unchanged. Where allocations/budgets are stored (finance service vs a
  new CFO store) is a Phase-2 decision.
- **Phase 3 — C-suite meetings.** The `POST /ask` A2A endpoint **is** the
  meeting seam: the future CEO/COO/CTO agents call it agent-to-agent. A meeting
  protocol/orchestrator is Phase 3.
- **Spec #5 — real Economic Capital → RAROC.** The `raroc` tool signature is the
  seam: spec #5 replaces the Basel-lite proxy behind it with a full
  economic-capital engine, unchanged for callers.
- **Durable memory** for the CFO (decisions, prior-period commentary) — deferred
  until there is state worth persisting (Phase 2+).

## Design principles honoured

- **Finance service is the single source of financial truth** — all math lives
  there as pure tested functions; the CFO is a thin reasoning layer.
- **Self-contained subsystem** — `cfo/` mirrors `agent/`/`finance/` and carries
  its own config, Dockerfile, k8s manifest, tests, and verify script.
- **Read-only until proven** — an autonomous officer that can only *observe* in
  Phase 1; write authority is added deliberately, gated, in Phase 2.
- **Tools do the arithmetic** — the LLM never computes a financial figure.

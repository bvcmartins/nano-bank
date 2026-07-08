# nano-bank personal manager (Phase 1)

An agentic **personal manager** for one nano-bank client. It knows everything about the
client (profile, accounts, balances, transactions, cards), answers and advises, and
**performs transactions on explicit instruction** behind a mandatory two-phase guardrail.
It runs on **GLM via Ollama-cloud** and is consumed as an **agent-to-agent HTTP endpoint**
(the "Agentic Branch"), with a Streamlit **test console** for driving it by hand.

## Key guarantees

- **The agent cannot pick the customer.** Every data/RAG/act tool is served by one MCP
  server; none of its tools take a `customer_id` or a token. The bound customer + the
  customer's nano-bank JWT come only from trusted transport headers
  (`X-Nano-Customer` / `X-Nano-Token`) that the LLM never sees.
- **Confirmation is mandatory, and it's a protocol property.** Money movement is
  two-phase: LLM-callable `propose_*` tools only record a *pending* action (no money
  moves); a separate, non-LLM `execute_action` runs only via an explicit `…/confirm`
  request — identically for the console and A2A callers. The model can propose but never
  self-confirm.
- **Writes go through the authenticated nano-bank API** (`:8081`), never direct DB — so
  ledger invariants hold. Reads come straight from Postgres (read-only).
- **Memory is a dedicated local Qdrant** (collection `nano_manager_memory`), per-customer
  and bi-temporal (superseded facts are invalidated, not deleted). It is **not** ragu.

## Prerequisites

- nano-bank API running on `:8081` and its Kind Postgres reachable via the host
  `kubectl port-forward` (see the repo root `CLAUDE.md`).
- An `OLLAMA_API_KEY` for `https://ollama.com/v1` (models `glm-5.2` → `glm-4.7` fallback).
- podman with `podman compose`.

## Run (containers)

```bash
cd agent
cp .env.example .env          # fill OLLAMA_API_KEY and BRANCH_SERVICE_TOKEN
./run-agent.sh                # builds + starts qdrant, mcp, api, console
```

- Test console: <http://localhost:8505> — click **Seed demo**, pick a client, then ask
  ("what's my balance?") or instruct ("transfer 25 from <acc> to <acc>") and **Confirm**.
- Agentic Branch API: `http://localhost:8086`
  - `POST /branch/clients/{customer_id}/message` `{ "message": "..." }`
    → `{ answer, thread_id, pending_action? }`
  - `POST /branch/clients/{customer_id}/actions/{action_id}/confirm` → executes
  - `POST /branch/clients/{customer_id}/actions/{action_id}/cancel`
  - `GET  /branch/clients/{customer_id}/profile`, `GET /health`
  - All guarded by `Authorization: Bearer $BRANCH_SERVICE_TOKEN`.

`qdrant` and `mcp` are **not** published to the host — only `api` and `console` are.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest agent -q            # offline suite (mocks + in-memory Qdrant)
.venv/bin/python -m pytest agent -q --run-live # + the two-phase end-to-end (needs the stack)
```

## Design & plan

- Spec: `../docs/superpowers/specs/2026-07-07-personal-manager-design.md`
- Plan: `../docs/superpowers/plans/2026-07-07-personal-manager-phase1.md`

Phase 2 (deferred) swaps the simple service token + seeded customer token for PR #19's
mandate + agent-token auth and moves reads/act onto the bank's mandate-pinned
`/api/v1/agent/*` surface. Phase 3 adds proactive monitoring.

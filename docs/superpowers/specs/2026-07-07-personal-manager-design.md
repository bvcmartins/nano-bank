# Nano-Bank Personal Manager — Design (Phase 1: read/advise foundation)

**Date:** 2026-07-07
**Status:** approved for spec review
**Scope:** Phase 1 only. Act (Phase 2) and Proactive (Phase 3) are follow-on specs.

## 1. Purpose

An agentic **personal manager** for a nano-bank client. It knows everything about
one client and answers/advises about their banking. It is consumed as a **service
endpoint** (the primary deliverable) and, in this phase, is limited to **reading and
advising** — no money moves.

Two consumers, one core:
- **The Agentic Branch** — an agent-to-agent HTTP API that other autonomous agents call.
- **A test/dev Streamlit client** — a throwaway harness to exercise the endpoint by hand.
  (The real human UI is a later, separate effort and is out of scope here.)

## 2. Requirements traceability

| Ask | Where it is satisfied |
|---|---|
| 1. Agentic personal manager | §5 agent core (ported `desktop_agent` harness) |
| 2. Knows everything about a client | §6 MCP server DB-read tools + server-side snapshot |
| 3. Accessed from UI and Agentic Branch | §7 FastAPI endpoint (primary) + Streamlit test client |
| 4. Harness similar to `desktop_agent.py` | §5 (managed ReAct agent, context hook, model factory) |
| 5. Local Qdrant RAG for interaction memories (not ragu) | §6 MCP RAG tools over a dedicated local Qdrant |
| 6. Ollama cloud backend, GLM5.2 → GLM4.7 fallback | §4 model factory + startup resolver |

## 3. Layout

New directory in the nano-bank repo. The manager is a Python service (the repo's
Rust API stays untouched in Phase 1).

```
agent/
  mcp_server.py     # MCP server: the ONLY gateway to DB + RAG, customer-scoped IN CODE
  nano_manager.py   # agent core: model factory, LangGraph agent, MCP-client wiring, assist()
  api.py            # FastAPI — the PRIMARY deliverable (the manager endpoint)
  ui.py             # Streamlit — TEST/dev harness only; talks to api.py like any other client
  requirements.txt
  .env.example
  README.md
  tests/            # unit + integration tests
```

## 4. Model factory (the one backend seam)

Ported from `desktop_agent._build_model` / `llm()`. Adds an `ollama_cloud` backend:

```python
from langchain_openai import ChatOpenAI
ChatOpenAI(base_url=OLLAMA_BASE_URL,   # https://ollama.com/v1
           api_key=OLLAMA_API_KEY, model=<resolved>, temperature=..., timeout=...)
```

**Startup model resolver.** A cheap 1-token probe of `MANAGER_MODEL` (default
`glm-5.2`). If it errors, fall back to `MANAGER_FALLBACK_MODEL` (default `glm-4.7`),
log which won, and cache the result. Every role (`reasoning` / `fast` / `summarizer`)
maps to the resolved id; both ids are env-overridable. If neither answers, startup
fails loudly (see §9).

The role-based `llm(role, reasoning=, temperature=, max_tokens=)` signature is kept
so the ported graph code reads identically to the source harness.

## 5. Agent core (`nano_manager.py`) — ported managed agent

`create_react_agent(llm, tools, prompt=MANAGER_PROMPT, pre_model_hook=context_hook,
checkpointer)`, following `desktop_agent.build_managed_agent`.

Deliberate divergences from `desktop_agent`:
- **No filesystem / bash / code tools.** They are unsafe and irrelevant for a banking
  agent. Phase-1 tools are exactly the customer-bound MCP tools from §6.
- **Persona (`MANAGER_PROMPT`):** a careful personal banking manager that answers
  *only* from the client's real data, never fabricates balances or transactions, says
  so plainly when it does not know, and — in this phase — advises only (it cannot and
  must not move money).

**Context hook** (ported `make_context_hook`): before the model call, it injects
(a) the client **snapshot** and (b) **recalled memories**, both obtained server-side
through the customer-bound MCP session (§6), and it bounds the message window. The
snapshot/recall are fetched by *code*, not by the LLM.

**Session identity.** `thread_id` is per (client, conversation). The LangGraph
checkpointer holds turn-by-turn state; long-term memory is the Qdrant RAG in §6.

**Entry point.** `assist(customer_id, message, thread_id) -> {answer, thread_id}`.
It opens a customer-bound MCP session (§6), loads snapshot + recall, runs the ReAct
loop, then stores the turn's memories through the same session. `customer_id` is a
parameter of this server-side function — it is **not** reachable by the LLM.

## 6. MCP server (`mcp_server.py`) — single customer-scoped gateway to DB + RAG

Both Postgres reads and Qdrant memory live behind **one MCP server**. This is the
security spine of the design.

### 6.1 Tools exposed to the LLM (no customer parameter)
- **DB-read (Postgres, read-only connection):**
  `get_profile()`, `get_accounts()`, `get_transactions(limit)`, `get_cards()`.
- **RAG (Qdrant):** `recall(query, k)`, `remember(fact, kind)`.

None of these take a `customer_id`. The agent therefore cannot *express* access to
another customer — the scoping is absent from the tool schema, not a prompt rule.

### 6.2 Customer binding — HTTP + trusted header (enforced in code)
- One long-running **streamable-HTTP** MCP server bound to **localhost** only.
- `api.py`, after authenticating the request (§7), opens a **per-request** MCP client
  session passing `customer_id` in a **trusted header** (e.g. `X-Nano-Customer`) that
  the LLM never sees or sets.
- The MCP server reads that header and stamps the bound `customer_id` into **every**
  SQL `WHERE customer_id = …` and **every** Qdrant payload filter. Any `customer_id`
  arriving in tool arguments is ignored — the header is the sole source of truth.
- Trust boundary: only `api.py` can reach the MCP port (localhost). The LLM influences
  the server only through tool *arguments*, which contain no customer.
- **Hardening path (not Phase 1):** a stdio MCP server spawned per session with
  `NANO_CUSTOMER_ID` in its env gives per-process isolation; documented for later.

### 6.3 Data source: DB reads
A **read-only** psycopg2 connection reusing `testing/viewer`'s `DB_*` config
(`DB_HOST` default `::1`, etc.). Queries join a customer's rows across
`customers` / `customer_addresses` / `accounts` / `transactions` / `cards`(credit_card
accounts) / holds, always filtered by the bound `customer_id`.

`snapshot(customer_id)` composes profile + accounts/balances + recent transactions +
cards into a compact text digest, used by the context hook (§5). It is a server-side
call, not an LLM tool.

### 6.4 Data source: RAG memory (`QdrantMemory`, local, not ragu, bi-temporal)
Same interface as the harness's `BiTemporalMemory` (`store` / `invalidate` /
`query_valid` / `recall`), so the ported context hook is unchanged.

- **Store:** a **dedicated local Qdrant** — its own container on its own port
  (default `:6335`, **not** ragu's), collection `nano_manager_memory`. Embeddings via
  **fastembed / CPU**.
- **Point payload:** `{customer_id, kind, source, fact, valid_from, valid_to, thread_id}`.
- **Scoping:** `recall` / `query_valid` filter by `customer_id` **AND** `valid_to IS
  NULL`. Superseded facts are **invalidated (stamped `valid_to`), not deleted** — a
  guarantee the old Vertex RAG-Engine path could not keep.
- **What gets written each turn:** the user request, the assistant answer, and any
  salient facts — all through the customer-bound session.

## 7. Surfaces

### 7.1 Agentic Branch API (`api.py`, FastAPI) — primary
- `POST /branch/clients/{customer_id}/message` — body `{message, thread_id?}` →
  `{answer, thread_id}`.
- `GET  /branch/clients/{customer_id}/profile` → the snapshot.
- `GET  /health` → resolver + Qdrant + Postgres status.
- **Auth (Phase 1):** a shared `BRANCH_SERVICE_TOKEN` bearer for calling agents. The
  `customer_id` from the (authenticated) request binds the MCP session (§6.2).
- Default port `:8086`.

**Caller vs agent scoping.** The external caller still names `customer_id` in the URL;
proving a *caller* may act for that client is **Phase-2 caller-authorization**. Phase-1's
guarantee is narrower and enforced: the **LLM/agent** cannot deviate from the bound
customer.

### 7.2 Streamlit test client (`ui.py`) — dev only
A dev dropdown of customers + a chat panel + the snapshot in the sidebar. It is a
**client of `api.py`**, not a second core. Default port `:8505`. Not a product surface.

## 8. Data flow

```
POST /branch/clients/{id}/message   (BRANCH_SERVICE_TOKEN)
  → api.py authenticates, derives customer_id
  → assist(customer_id, message, thread_id):
       open MCP session bound to customer_id (X-Nano-Customer header)
       snapshot = MCP get_profile/accounts/transactions/cards   (server-side)
       memories = MCP recall(...)                                (server-side)
       context hook injects snapshot + memories, bounds window
       ReAct loop on GLM (ollama.com/v1); read tools hit Postgres via MCP
       MCP remember(request), remember(answer), remember(salient facts)
  → {answer, thread_id}
```

## 9. Error handling

- **Backend:** resolver picks glm-5.2/glm-4.7; if neither answers, startup fails with a
  clear message. Per-request model errors return a graceful reply, not a 500 stacktrace.
- **DB:** read-only connection; a tool failure returns an error string so the agent
  degrades (answers from memory/known context) rather than crashing.
- **Memory:** writes never raise (mirrors `desktop_agent.remember`).
- **Unknown customer_id:** `404` from the API / a notice in the test UI.
- **MCP:** if the customer-bound session cannot open, the request fails closed (no
  unscoped access is ever attempted).

## 10. Testing

- **Unit**
  - `QdrantMemory`: store → recall → invalidate; and **cross-customer isolation**
    (a fact stored for customer A is never recalled for customer B).
  - Model resolver: primary-ok, primary-fails-fallback-ok, both-fail (mocked probe).
  - MCP scoping: a tool call is answered only for the header-bound customer; a
    `customer_id` injected into tool args is ignored.
  - The DB-read layer's SQL (§6.3) against a seeded test DB (or fixture).
- **Integration**
  - Seed a client via `testing/generator`; `POST` to the a2a `/message`; assert the
    answer cites the client's real balance and that memory persists across two calls
    (turn 2 can recall a fact from turn 1).
- **Health:** `GET /health` (and a `--health` CLI) probe ollama-cloud + Qdrant + Postgres.

## 11. Config (`.env.example`)

| Var | Default | Purpose |
|---|---|---|
| `OLLAMA_API_KEY` | — | ollama.com auth |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | OpenAI-compat endpoint |
| `MANAGER_MODEL` | `glm-5.2` | primary model |
| `MANAGER_FALLBACK_MODEL` | `glm-4.7` | fallback model |
| `QDRANT_URL` | `http://localhost:6335` | dedicated local Qdrant (not ragu) |
| `QDRANT_COLLECTION` | `nano_manager_memory` | memory collection |
| `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` | viewer defaults (`::1`, …) | read-only DB access |
| `NANO_BANK_API` | `http://localhost:8081` | reserved for Phase 2 (act) |
| `BRANCH_SERVICE_TOKEN` | — | a2a bearer for calling agents |
| `MCP_URL` | `http://localhost:8087/mcp` | localhost-only MCP server |
| `BRANCH_PORT` / `UI_PORT` | `8086` / `8505` | API / test-UI ports |

## 12. Out of scope (later phases)

- **Phase 2 — Act:** API-write tools (transfer/deposit/withdrawal/card) via the
  authenticated nano-bank API on :8081, behind Agentic Governance (confirmation,
  amount limits, audit log), plus **caller-authorization** (§7.1).
- **Phase 3 — Proactive:** a monitor scanning the client picture for signals
  (low balance, unusual activity) and surfacing alerts.
- The real human-facing UI (this phase ships only the dev test client).
- stdio-per-session MCP isolation (§6.2 hardening path).

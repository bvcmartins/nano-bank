# 🏦 nano-bank

> Why not vibe-coding a bank?

A toy Canadian challenger bank, built to explore what a production-shaped banking
stack looks like end to end: a **Rust (axum) API** over a **double-entry
PostgreSQL** subledger on a local **Kind** cluster, posting its general ledger to
a swappable **core** service, screening every money movement through an external
**fraud engine**, and operated by a set of **LLM agents** (a personal manager and
an agentic C-suite).

> [!WARNING]
> Learning/experimental project — **not** for handling real money. Default
> credentials, the JWT secret and service tokens are committed in plaintext for
> local dev convenience. Don't deploy this anywhere real.

## The ecosystem

nano-bank is the hub of six sibling repositories. It owns customers, accounts,
the payment rails and the per-account subledger; everything else plugs in through
a narrow port or a service-token API.

```
                        ┌──────────────────────────┐
                        │  nano-bank-world-model   │  synthetic population + fraud
                        │  (Python, offline/replay)│  typologies, ground-truth labels
                        └─────┬───────────────┬────┘
            realize: drives   │               │  ground truth:
            the bank's APIs   │               │  POST /v1/outcomes
                              ▼               ▼
┌──────────────────┐   ┌─────────────────────────────┐   FraudCheck port   ┌──────────────────────────┐
│ nano-bank-crm    │──►│  nano-bank  (this repo)     │────────────────────►│ nano-bank-fraud-engine   │
│ (TS, :3000/3100) │   │  Rust API :8081             │  /v1/decisions      │ (Python, :8092)          │
│ back-office reads│◄──│  rails · subledger · agents │  /v1/rescore …      │ rules → policy → action  │
│ agent mandates   │   └─────────────┬───────────────┘                     └──────────────────────────┘
└──────────────────┘                 │  Ledger port (CORE_BACKEND)
        ▲ COO lever:                 ├──────────────────────┐
        │ provision mandate          ▼                      ▼
        │                ┌───────────────────────┐ ┌────────────────────────┐
   operations MCP        │ nano-bank-modern-core │ │ nano-bank-legacy-core  │
                         │ Rust GL, :8091        │ │ Java ERP-style, :8090  │
                         └───────────────────────┘ └────────────────────────┘
```

| Repo | Role | How nano-bank talks to it |
|------|------|---------------------------|
| [`nano-bank-modern-core`](../nano-bank-modern-core) | Clean Rust general ledger (`journal_entry`/`journal_line`, REST) | `ModernLedger` adapter, `CORE_BACKEND=modern` (default), `MODERN_CORE_URL` |
| [`nano-bank-legacy-core`](../nano-bank-legacy-core) | Cleanroom ERP-style document-posting core (Java/Spring; REST, SOAP, OData, IDoc) | `LegacyLedger` adapter, `CORE_BACKEND=legacy`, `LEGACY_CORE_URL` |
| [`nano-bank-fraud-engine`](../nano-bank-fraud-engine) | Real-time transaction risk decisioning (hard gates → soft signals → policy table) | `FraudCheck` port, `NANO_BANK__FRAUD__BACKEND=engine` |
| [`nano-bank-crm`](../nano-bank-crm) | Metadata-driven CRM where AI agents are principals under mandates | CRM reads nano-bank's **back-office** plane; the COO agent provisions CRM mandates |
| [`nano-bank-world-model`](../nano-bank-world-model) | Simulation that generates a labelled fraud corpus and replays it against the live bank | Drives nano-bank's customer/rail APIs; feeds ground truth to the fraud engine |

### Core banking: the Ledger port (legacy-core ↔ modern-core)

General-ledger posting is backend-agnostic. Handlers post through a small
`Ledger` trait (`api/src/ledger/`) that speaks **semantic** accounts (`bank`,
`receivable`, `payable`, `revenue`, `customer_deposits`, `interest_income`, …),
a `Direction` (`debit`/`credit`) and `Decimal` money. Each adapter maps those onto
its backend:

```
handlers  ──►  Ledger (trait)
                 ├── ModernLedger ──HTTP──►  modern core :8091   POST /entries, GET /balances   (GL codes BANK, AR, AP, …)
                 └── LegacyLedger ──HTTP──►  legacy core :8090   /api/v1/documents, /api/v1/gl-balances
                                                                 (0000xxxxxx accounts, S/H indicator, company code 1000)
```

The adapter is chosen once at startup:

```bash
CORE_BACKEND=modern MODERN_CORE_URL=http://localhost:8091   # default
CORE_BACKEND=legacy LEGACY_CORE_URL=http://localhost:8090
```

The **subledger vs GL split**: nano-bank keeps per-account balances, holds and
limits locally (`transactions` / `transaction_entries`, enforced by triggers);
the core is the GL of record and only sees **aggregate** postings. Every GL post
happens *inside* the local DB transaction before commit, so if the core can't
record it the operation fails (503) instead of letting the two drift. Postings
are wired for cards (capture/settle, interchange), deposits/withdrawals, all
three external rails, the interest/NIM engine and fees, and lending. Transfers
between customers are local-only (net-zero GL effect).

Both cores carry the same chart, including the bank-economics and accrued-interest
GL accounts used by the finance engine, so the same request lands in either:

```bash
curl -X POST localhost:8081/api/v1/ledger/journal -H 'content-type: application/json' -d '{
  "lines":[{"account":"bank","direction":"debit","amount":250.00},
           {"account":"revenue","direction":"credit","amount":250.00}]}'
curl localhost:8081/api/v1/ledger/balances
```

`testing/verify-gl-expansion.sh` and `testing/verify-nim-engine.sh` are
cross-backend smoke tests — run them once per `CORE_BACKEND`.

### Fraud engine: the FraudCheck port

`api/src/fraud/` mirrors the Ledger pattern: a `FraudCheck` trait with an HTTP
`EngineFraudCheck` adapter and a no-op, selected by `[fraud] backend`
(default **`off`**, which is also the operational kill switch).

- **Screening** — every customer- or agent-initiated money movement calls
  `fraud::gate::screen()` **before** its DB transaction opens (no row locks
  across a network call) → `POST /v1/decisions`. The engine returns an *action*,
  never a score: `allow`, `block`, `hold_review`, `challenge`, `delay_and_warn`
  (the last two currently collapse to under-review).
- **Declines are opaque** to the customer (`TRANSACTION_DECLINED` /
  `TRANSACTION_UNDER_REVIEW`). A **hold** parks the movement; the customer can
  follow it via `GET /api/v1/reviews/:id`, and the bank asks the engine for the
  case outcome via `GET /v1/decisions/{operation_id}/disposition`.
- **Failure matrix** — 150 ms budget and a circuit breaker. If the engine is
  unreachable, amounts ≤ `fail_closed_above` ($1,000) **fail open** (proceed,
  then `POST /v1/rescore`); larger amounts **fail closed** (503).
- **Agent context** — agent-initiated movements carry the agent id, mandate id
  and step-up/cap-override details, and are tagged channel `agentic_branch`.
- **Denial feedback** — refused agent actions are written to an outbox and
  delivered to `POST /v1/outcomes` by `POST /api/v1/fraud/admin/flush-denials`,
  driven by `k8s/fraud-denial-drainer-cronjob.yaml` every 5 minutes.
- **Linking** — the money row's metadata stores the engine's `operation_id` /
  `decision_id`; `GET /api/v1/fraud/admin/transactions/:id/fraud-link` (and the
  per-rail variant) exposes it for reconciliation.

```toml
# api/config/default.toml  (override with NANO_BANK__FRAUD__*)
[fraud]
backend = "off"                       # "engine" to screen
engine_url = "http://localhost:8092"
timeout_ms = 150
fail_closed_above = "1000.00"
service_token = "dev-service-token"   # = engine's FRAUD_ENGINE__AUTH__SERVICE_TOKEN
outcomes_timeout_ms = 2000
```

The engine's own admin plane (`/admin/v1/*` — lists, review cases, policy and
rules activations) runs on a separate credential; see its README and
`design/INTEGRATION_DESIGN.md` there for the full contract.

### CRM: back-office plane + agent mandates

Two directions of integration with [`nano-bank-crm`](../nano-bank-crm):

1. **CRM → bank (reads).** The CRM is a back-office consumer. It mints a service
   token (`POST /api/v1/auth/service-token`) and reads across customers via the
   read-only **`/api/v1/back-office/*`** plane (customers, accounts, balances,
   transactions, plus `ops/*` views). Contacts are matched to bank customers **by
   email**. When the bank is down the CRM degrades to cached figures with a
   `bank unavailable` banner. In the CRM:
   ```bash
   BANKING_DRIVER=live NANO_BANK_URL=http://localhost:8081 \
   NANO_BANK_CLIENT_SECRET=nano-bank-visa-network-secret-change-me \
   PORT=3100 pnpm dev        # 3100: the bank's own UI already uses 3000
   ```
2. **Bank agents → CRM (mandates).** The COO's operations MCP server
   (`operations/crm_client.py`) exposes the `execute_provision_crm_mandate`
   lever, which calls the CRM's `POST /api/agent/provision` to get-or-create a
   CRM contact and a mandate scoped to one customer, so the **personal manager**
   can read and log CRM activity through the CRM's own generated MCP tools. The
   manager only looks up and mints tokens against existing mandates; provisioning
   is the COO's call. Configure with `CRM_BASE_URL`, `CRM_TENANT_SLUG`,
   `CRM_PROVISIONING_TOKEN` (must match the CRM's `CO_PROVISIONING_TOKEN`). The
   manager-side consumer is planned in
   `docs/superpowers/plans/2026-08-30-personal-manager-crm-access.md`.

### World model: synthetic traffic and ground truth

[`nano-bank-world-model`](../nano-bank-world-model) simulates a Canadian
retail-bank population with injected fraud typologies (ATO, APP scam, mule,
first-party, synthetic ID, agentic abuse, AML patterns) and benign confounders.

- **Generate** — offline: writes a labelled corpus (`intents.parquet`,
  `outcome_events.parquet`, …) in the fraud engine's field vocabulary.
- **Realize** — replays a scenario against a running nano-bank, moving money
  through every rail, then reconciles the GL; the exit code is non-zero unless it
  reconciles:
  ```bash
  uv run world-model realize --scenario scenarios/hero.yaml \
      --bank-url http://localhost:8081 --engine-url http://localhost:8092 \
      --run-tag demo-1 --out ./realized
  ```
- **Simulated time** — the world model dates requests with an
  `x-simulated-time` header so fraud velocity is measured at the simulated
  instant. nano-bank honours it only when
  `NANO_BANK__FRAUD__ACCEPT_SIMULATED_TIME=true` (off by default).
- **Closing the loop** — it reads back the bank's fraud link per transaction
  (`/api/v1/fraud/admin/.../fraud-link`) and posts ground-truth labels to the
  engine's `/v1/outcomes`. The fraud summary is observational: decisions are
  still the bank's (via the engine), never the world model's.

## What's in this repo

| Area | What it is |
|------|-----------|
| **Accounts & money** | Customers, chequing/savings/credit-card accounts, deposit / withdrawal / transfer (idempotent), per-account limits and daily summaries |
| **Cards** | Issuer rails: authorize (hold) → capture → settle, interchange recognized at capture |
| **Interac e-Transfer** | Send, autodeposit, claim (security Q&A, 3-strike lock), decline, cancel, expiry sweep, saved payees, notification outbox + drainer CronJob |
| **AFT / EFT** | Batch rail over ACSS: direct-deposit credits, PAD debits under mandates, CPA-005-style files, settlement, NSF rejects, returns |
| **Lynx** | High-value RTGS wires with finality: pacs.008/009, camt.056/029 recalls (ISO 20022, round-trippable) |
| **Finance / NIM** | Daily ACT/365 accrual, monthly capitalisation, maintenance and e-transfer fees, tagged by product/cost-centre |
| **Lending** | Loan applications, disbursement, repayment, interest accrual |
| **Agentic banking** | Agent registration, customer-granted **mandates** (scoped, capped, expiring, revocable), mandate-scoped agent API, step-up approvals for over-cap transfers, agent action ledger, consent UI at `/app` |
| **Back office & ops** | Service-token cross-customer reads, ops views (float, rails, exceptions, cards, declines), ops levers (cut AFT batch, sweep e-transfers, reject stale wires, flush notifications) |
| **Personal manager** (`agent/`) | GLM/Ollama-cloud LangGraph agent behind a customer-scoped MCP gateway, confirm-gated money movement, Qdrant memory. A2A API `:8086`, console `:8505` |
| **Agentic C-suite** | `ceo/`, `cfo/`, `coo/`, `cto/`, `cxo/`, `coder/` agents on a shared `csuite/` harness with grounded-number verification; domain MCP servers in `finance/`, `operations/`, `platform_mcp/`, `cx/` |
| **UI** (`ui/`) | Next.js customer app on `:3000` (demo login `demo@nano.bank` / `Demo-Pass-2026`) |
| **Demos** (`demos/`) | Ten guided demos from onboarding to the CEO boardroom — see [`demos/README.md`](demos/README.md) |

Not yet implemented: KYC document upload and the `security/*` endpoints
(sessions, devices, device trust) still return `TODO` stubs.

## Repository layout

```
nano-bank/
├── api/                 # Rust API (axum 0.7, sqlx, rust_decimal, argon2, JWT)
│   ├── src/handlers/    # one module per area (cards, interac, aft, lynx, finance, loans, agents, …)
│   ├── src/ledger/      # Ledger port + modern/legacy adapters
│   ├── src/fraud/       # FraudCheck port, engine adapter, screening gate
│   ├── src/rails/       # Rail port (hold/release/refund/accept_inbound) + per-rail system accounts
│   ├── src/aft/ src/lynx/  # CPA-005 and ISO 20022 codecs
│   ├── tests/           # integration tests (need the DB)
│   └── config/default.toml
├── src/core/tables/     # PostgreSQL DDL 00–16, loaded by the Kind init Job
├── k8s/                 # Kind config, Postgres, bank-api, CronJobs, cross-cluster core wiring
├── agent/               # personal manager (LangGraph + MCP)
├── ceo/ cfo/ coo/ cto/ cxo/ coder/ csuite/   # agentic C-suite
├── finance/ operations/ platform_mcp/ cx/    # domain MCP servers the officers use
├── ui/                  # Next.js customer app
├── demos/               # guided demos
├── testing/             # data generator, Visa/Interac/AFT/Lynx simulators, Streamlit viewer, smoke scripts
├── scripts/             # deploy-all, start/stop, e2e
└── docs/                # specs and plans
```

## Getting started

Prerequisites: Docker, `kind`, `kubectl`, a Rust toolchain; Python/`uv` for the
agents and simulators; Node for the UI.

### Full stack in Kind

```bash
./scripts/setup-k8s.sh     # one-time host setup
./scripts/deploy-all.sh    # modern core (its own cluster) + Postgres + bank-api + agent
```

### Local inner loop

```bash
# 1. Database (Kind) — note: the API connects on IPv6 ::1, not 127.0.0.1
./k8s/deploy.sh
kubectl port-forward -n nano-bank svc/postgres-service 5432:5432 &

# 2. A core — start it before the API, or every posting returns 503
(cd ../nano-bank-modern-core && docker compose up -d)          # :8091
# or: (cd ../nano-bank-legacy-core && ./start-core.sh)          # :8090

# 3. Optional: the fraud engine
(cd ../nano-bank-fraud-engine && ./start-engine.sh)            # :8092

# 4. The API
cd api && NANO_BANK__FRAUD__BACKEND=engine cargo run           # :8081
curl -s localhost:8081/health
```

Config is layered: `config/default.toml` → `config/{RUN_MODE}.toml` →
`config/local.toml` → env vars prefixed `NANO_BANK__` with `__` as separator
(e.g. `NANO_BANK__SERVER__PORT=8082`). `CORE_BACKEND`, `MODERN_CORE_URL` and
`LEGACY_CORE_URL` are read directly.

## Ports

| Service | Port | Repo |
|---------|------|------|
| nano-bank API | 8081 | this repo (`api/`) |
| nano-bank PostgreSQL | 5432 (NodePort 30432) | this repo, Kind |
| nano-bank UI | 3000 | this repo (`ui/`) |
| Personal manager API / console | 8086 / 8505 | this repo (`agent/`) |
| Testing viewer | 8504 | this repo (`testing/viewer`) |
| Legacy core | 8090 | `nano-bank-legacy-core` |
| Modern core | 8091 (DB 5435) | `nano-bank-modern-core` |
| Fraud engine | 8092 (DB 5436, Redis 6379) | `nano-bank-fraud-engine` |
| CRM web | 3000 → use 3100 next to the bank UI (DB 5433) | `nano-bank-crm` |

## API overview

Full HTML docs at `http://localhost:8081/docs`. Under `/api/v1`:

| Area | Prefix |
|------|--------|
| Auth (customer login/refresh/logout, service token, agent token) | `/auth` |
| Customers, saved Interac recipients | `/customers` |
| Accounts | `/accounts` |
| Transactions | `/transactions` |
| Cards | `/cards` |
| Interac / AFT / Lynx | `/interac`, `/aft`, `/lynx` |
| Ledger (via port) | `/ledger` |
| Finance (accrue, capitalise) | `/finance` |
| Loans | `/loans` |
| Agents, mandates, mandate-scoped agent API, approvals, agent ledger | `/agents`, `/mandates`, `/agent`, `/approvals`, `/agent-ledger` |
| Fraud reviews / fraud admin | `/reviews`, `/fraud` |
| Back office and ops levers (service token) | `/back-office`, `/ops-levers` |
| Security (stubbed) | `/security` |

Auth planes: **customer** JWTs for customer endpoints; a **service** token
(client-credentials, `security.service_client_secret`) for network, admin and
back-office endpoints; short-lived **agent** tokens bound to a mandate.

## Testing

```bash
cd api && cargo test                 # integration tests (DB must be up)
testing/run-testing.sh               # generator + rail simulators + viewer
testing/transactions_smoke.sh        # REST smoke
testing/cleanup.sh [--dry-run]       # wipe customer data (system GL accounts self-heal)
```

The Python packages (`agent/`, `csuite/`, officers, MCP servers) each have a
`tests/` directory run with `pytest`. CI is in `.github/workflows/ci.yml`.

## More docs

- [`CLAUDE.md`](CLAUDE.md) and [`api/CLAUDE.md`](api/CLAUDE.md) — internals and gotchas
- [`src/core/tables/README.md`](src/core/tables/README.md) — schema
- [`k8s/README.md`](k8s/README.md) — cluster
- [`agent/README.md`](agent/README.md) — personal manager
- [`docs/specs/`](docs/specs) — rail, finance and fraud design specs

## License

See [`LICENSE`](LICENSE).

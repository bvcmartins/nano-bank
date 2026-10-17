# CLAUDE.md — nano-bank

Guidance for working in this repo. nano-bank is a toy Canadian challenger bank:
a Rust/axum API over a PostgreSQL 16 double-entry subledger on a local Kind
cluster, posting its general ledger to a swappable core, screening money
movement through an external fraud engine, and operated by Python LLM agents.
This file focuses on the parts that aren't obvious from the code; `api/CLAUDE.md`
covers API internals and `README.md` the sibling-repo ecosystem.

## Commands

Rust, from `api/`:

```bash
cargo check | build | fmt | clippy
cargo run            # API on 0.0.0.0:8081
cargo test           # integration tests in api/tests/ — need the DB (and a core) up
```

Infrastructure, from the repo root:

```bash
./scripts/deploy-all.sh                                           # full stack in Kind
./k8s/deploy.sh                                                   # Postgres + DDL init Job (+ bank-api)
kubectl port-forward -n nano-bank svc/postgres-service 5432:5432  # required before cargo run
kubectl exec -it -n nano-bank deployment/postgres -- psql -U nanobank_user -d nano_bank_db
testing/cleanup.sh [--dry-run]   # TRUNCATE customers CASCADE (system GL accounts self-heal)
```

Python packages (`agent/`, `csuite/`, `ceo/` `cfo/` `coo/` `cto/` `cxo/`
`coder/`, `finance/`, `operations/`, `platform_mcp/`, `cx/`) each have a
`tests/` dir run with `pytest`.

## Core invariants (read before touching money code)

### Request flow

```
HTTP request
  → axum middleware (CORS, gzip, 30s timeout, 10MB body limit, tracing)
  → handler fn (State<AppState>, ...) -> Result<_, AppError>
  → [fraud::gate::screen() for money movement — BEFORE the DB tx opens]
  → inline sqlx SQL in a PgTx; Ledger port GL post inside the same tx
  → PostgreSQL triggers mutate balances / enforce invariants
  → AppError serialises as { "error": { "code", "message", "details" } }
```

`AppState` (`handlers/mod.rs`) holds the `PgPool`, `Settings`, the `ledger`
adapter and the `fraud` adapter.

### Database pattern

No ORM; `repositories/`, `services/` are empty placeholders. SQL is inline in
handlers. Postgres error codes map directly: `23505` → `Conflict`, `23503` /
`23514` → `BadRequest`.

### Double-entry (critical)

Both legs of a local transaction **must be inserted in one multi-row `INSERT`**
— use `post_two_legged()` in `handlers/cards.rs` (shared by other handlers).
`trigger_validate_transaction_balance` (AFTER INSERT on `transaction_entries`)
rejects an unbalanced transaction, so one-leg-at-a-time inserts fail.

Other triggers (`src/core/tables/06_triggers.sql`):
- `trigger_update_account_balance` — maintains `accounts.balance` and
  `balance_before/after`. Never update balances directly. It does **not**
  maintain `available_balance`; handlers recompute that on customer accounts.
- `trigger_generate_account_number` — overwrites `account_number`, so system/GL
  accounts are keyed by `(customer_id, account_type)`, never by number.

System accounts (`VISA_CLEARING`/`BANK_SETTLEMENT` under `system@nano.bank`,
plus per-rail clearing/settlement accounts) carry a $1T overdraft, are
bootstrapped idempotently at startup, and are re-resolved per request because
`testing/cleanup.sh` rebuilds them.

### Enum serialisation quirk

Enums like `KycStatus` use `#[sqlx(rename_all = "snake_case")]` but no serde
rename, so JSON is PascalCase (`"Pending"`).

### Bruno collection

Bruno `.bru` files need `body: json` and `auth: inherit` **inside the `post {}`
block** next to the URL, or Bruno ignores `body:json {}`. Copy the working
`_2` files in `bruno/`.

### Config

Layers: `api/config/default.toml` → `config/{RUN_MODE}.toml` →
`config/local.toml` → env vars `NANO_BANK__SECTION__KEY`. `CORE_BACKEND`,
`MODERN_CORE_URL`, `LEGACY_CORE_URL` are read directly. `default.toml` is the
source of truth for local dev credentials; `database.host` is `::1` on purpose.

## Big picture: the kernel split

nano-bank's general-ledger posting is **backend-agnostic**. The app posts
accounting entries through a small `Ledger` port, and the actual ledger lives in
one of two **interchangeable core services**, chosen at startup by an env var:

```
nano-bank app (this repo)            http://localhost:8081
  api/src/handlers/ledger.rs  ─┐
  api/src/handlers/cards.rs   ─┴─►  Ledger port (api/src/ledger/)
                                      ├── ModernLedger ──HTTP──► modern core  :8091
                                      └── LegacyLedger ──HTTP──► legacy core   :8090
  CORE_BACKEND=modern | legacy   picks the adapter at startup
```

The two cores are separate repos and run as peers:
- **`nano-bank-modern-core`** — a clean Rust/axum general-ledger service.
- **`nano-bank-legacy-core`** — a cleanroom ERP-style financial core (Java/Spring)
  that exposes document-posting contracts (REST/SOAP/OData/IDoc) using authentic,
  cryptic technical field names. Treat those names as neutral identifiers; do not
  describe in code or docs what product they resemble.

The port speaks **semantic** terms (an `Account` role like `bank`/`receivable`/
`revenue`, a `Direction` of `debit`/`credit`, `Decimal` money). Each adapter maps
those to its backend's numbering (modern GL codes like `BANK`/`AR` vs the legacy
core's `0000xxxxxx` numbers + `S/H` indicator), so nano-bank never needs to know
either backend's account scheme.

## Where things live

- `api/` — the Rust service (see `api/CLAUDE.md` for internals).
- `api/src/ledger/` — the `Ledger` port (`mod.rs`) and the two adapters
  (`modern.rs`, `legacy.rs`).
- `api/src/handlers/ledger.rs` — `POST /api/v1/ledger/journal`, `GET /api/v1/ledger/balances`.
- `api/src/handlers/cards.rs` — the card rails (authorize/capture/settle).
- `src/core/tables/` — the PostgreSQL DDL (loaded by the Kind init Job).
- `api/src/handlers/interac_payees.rs` — sender-side **saved Interac payees**
  (`/api/v1/customers/interac-recipients`), an address book distinct from the
  Interac rail; sending still goes through `/api/v1/interac/etransfers`.
- `k8s/` — Kind cluster + Postgres manifests.
- `api/src/fraud/` — the `FraudCheck` port (`mod.rs`), the engine adapter
  (`engine.rs`), the no-op, and the screening gate (`gate.rs`).
- `api/src/rails/` — the `Rail` port and per-rail system accounts.
- `api/src/handlers/back_office.rs` — read-only service-token plane the CRM and
  the C-suite consume; `ops_levers.rs` — the COO's write levers.
- `testing/` — data generator, Visa/Interac/AFT/Lynx simulators, Streamlit
  viewer (`:8504`), smoke/verify scripts.
- `ceo/ cfo/ coo/ cto/ cxo/ coder/` on the shared `csuite/` harness, using the
  domain MCP servers in `finance/`, `operations/`, `platform_mcp/`, `cx/`.
- `ui/` — Next.js customer app (`:3000`); `demos/` — guided demos.
- `agent/` — the Python **personal manager**: a GLM/Ollama-cloud LangGraph agent
  behind a customer-scoped MCP gateway (DB reads + Qdrant memory + two-phase,
  confirm-gated money movement), plus a skill system and Interac saved-payees +
  confirm-gated e-Transfer send (over the real rail). Runs in-cluster (its own
  k8s manifests under `agent/k8s/`); FastAPI A2A on `:8086`, Streamlit console
  `:8505`. See `agent/README.md`.
- `scripts/` — lifecycle scripts (`deploy-all.sh`, `setup-k8s.sh`, `start-*.sh`).

## Running the stack

1. **Database** (Kind): `./k8s/deploy.sh` (or `./setup-k8s.sh`). The DB ends up on
   host **`::1:5432`** — note the IPv6 loopback; `127.0.0.1:5432` does *not* work
   (a dead docker-proxy listens there). Connection details are in
   `api/config/default.toml`.
2. **A core** — start at least one:
   - modern: in `nano-bank-modern-core`, `docker compose up -d db` then
     `DATABASE_URL=postgres://core:core@localhost:5435/modern_core cargo run` (`:8091`).
   - legacy: in `nano-bank-legacy-core`, `./start-core.sh` (`:8090`).
3. **nano-bank**: `cd api && cargo run` (`:8081`). Pick the backend with env:
   - `CORE_BACKEND=modern MODERN_CORE_URL=http://localhost:8091`
   - `CORE_BACKEND=legacy LEGACY_CORE_URL=http://localhost:8090`
   Defaults: backend `modern`, the two URLs above.

## Trying the swap

```bash
# the SAME request posts to whichever core is configured
curl -X POST localhost:8081/api/v1/ledger/journal -H 'content-type: application/json' -d '{
  "lines":[{"account":"bank","direction":"debit","amount":250.00},
           {"account":"revenue","direction":"credit","amount":250.00}]}'
curl localhost:8081/api/v1/ledger/balances
```

Restart nano-bank with the other `CORE_BACKEND` and the same call lands in the
other core (a new entry id / `belnr`).

## Cards: subledger vs general ledger

`cards.rs` keeps a **per-card subledger locally** (the `transactions` /
`transaction_entries` tables, plus `account_holds`) because the GL core only has
**aggregate** accounts, and per-card balances drive credit-limit checks
(`available = overdraft_limit − balance − holds`).

On top of that, `capture` and `settle` post the **aggregate GL effect** to the
core via the port (capture: debit CardReceivable / credit Payable; settle: debit
Payable / credit Bank), recording the core's document id in
`transactions.metadata.gl_entry`. The GL post happens inside the capture/settle
DB transaction, before commit — so if the core can't record it, the operation
fails rather than letting the local subledger and the GL drift. `authorize` is
local-only (a hold; no money moves).

`transactions.rs` (deposit/withdrawal/transfer + history) is implemented.
Deposit and withdrawal move value across an internal **`EXTERNAL_CASH`** account
(a chequing account under a synthetic `cash@nano.bank` customer, $1T overdraft)
and post the aggregate effect through the port (deposit: debit `Bank` / credit
`CustomerDeposits`; withdrawal the reverse). A **transfer is local-only** — both
customer accounts map to the same `CustomerDeposits` GL role, so the net GL effect
is zero. All
three enforce balance/status/type checks and the `account_limits` counters, and
update `daily_transaction_summaries`; transfer honors an `idempotency_key`.

## Interac e-Transfer rail

The first **external payment rail**, built on a small `Rail` port that sits
*beside* the `Ledger` port (`api/src/rails/`, see also `api/CLAUDE.md` and the
`.claude/skills/nano-bank-rails` skill; design spec in
`docs/specs/2026-07-04-interac-rail-foundation-design.md`).

- **Rail port** (`rails/mod.rs`): the verbs `hold` / `release` / `refund` /
  `accept_inbound`, each taking `&mut PgTx` so the local double-entry and the
  aggregate GL post commit or roll back together (503 if the core is down). A
  `Destination` is `Internal(account)` (a nano-bank customer) or
  `External(institution)` (settles via the settlement account).
- **Interac system accounts** (`rails/interac.rs`): a *separate* synthetic
  customer `interac@nano.bank` owns `INTERAC_CLEARING` (chequing, holds in-flight
  funds) and `INTERAC_SETTLEMENT` (savings, the interbank position), both with a
  $1T overdraft. Distinct from the card rails' `system@nano.bank` because GL
  accounts are keyed by `(customer, account_type)`.
- **Lifecycle** (`handlers/interac.rs`): send → held in `INTERAC_CLEARING` →
  autodeposit (registered handle) / claim (security Q&A, argon2, 3-strike lock) /
  decline / cancel / expire (sweep). Inbound: autodeposit fast-path
  (`accept_inbound`) or held-then-claim. Notifications go to the
  `interac_notifications` **outbox** table (no real email/SMS); the admin-plane
  **drainer** (`POST /admin/flush-notifications`) consumes it — claims undelivered
  rows race-safely (`FOR UPDATE SKIP LOCKED`, an atomic `delivery_attempts += 1`
  claim so a crashed send just retries next tick), hands each to a delivery seam
  (stubbed — the SMTP/SMS plug point), and dead-letters a row past its retry cap.
  Triggered by a k8s CronJob (`k8s/interac-notification-drainer-cronjob.yaml`).
- **Auth planes**: the *enforced* boundary is customer (`/etransfers`,
  `/autodeposit`) vs service token — `AuthenticatedService` checks only
  `role == Service`. Within the service token, **network** (`/network/inbound`,
  `/network/etransfers/:id/settle` — driven by
  `testing/interac/interac_simulator.py`) and **admin**
  (`/admin/sweep-expired`, `/admin/flush-notifications`) are a routing/naming
  convention, not separate credentials: the same secret opens both. Splitting
  them would need a distinct admin credential. The viewer (`testing/viewer`) has
  an Interac tab.
- **`available_balance` note**: the balance trigger maintains only `balance`, so
  the handlers hand-recompute `available_balance` around rail posts on **customer**
  accounts; the system clearing/settlement accounts intentionally keep it at 0
  (they float on the $1T overdraft).
- **Known v1 gaps / follow-ups**: the autodeposit registration endpoint always
  sets autodeposit, so there is no API path yet to register a handle *without*
  autodeposit (the "registered-no-autodeposit" internal-claim branch isn't
  API-reachable). Deferred: shared `account_limits` integration (pending PR #15),
  the ACSS-style `INTERAC_SETTLEMENT`→`Bank` settlement sweep (lands with the AFT
  rail), and Request Money.

## AFT / EFT batch rail

The second external rail (`handlers/aft.rs`, `rails/aft.rs`, `aft/cpa005.rs`) —
Canada's **batch** rail (Automated Funds Transfer over ACSS). Unlike Interac's
per-message model, AFT is file-based and batched. Design spec:
`docs/specs/2026-07-05-aft-eft-rail-design.md`.

- **Own system accounts (decoupled from cards/Interac):** `aft@nano.bank` owns
  `AFT_CLEARING` (chequing) + `AFT_SETTLEMENT` (savings), $1T overdraft. Reuses
  the `Rail` port (`hold`/`release`/`refund`/`accept_inbound`) for the money moves.
- **Two products:** direct-deposit **credits** (pay external accounts / receive
  inbound) and **pre-authorized debits (PAD)**, gated by a `pad_mandates` record
  (a payer authorizes a biller, with an amount cap; revocable).
- **Lifecycle:** originate credits/debits → they accrue in the single open
  outbound batch → `POST /aft/batches/:id/submit` closes it and emits a real
  **CPA-005-style fixed-width file** (`aft/cpa005.rs`, round-trippable) →
  `POST /aft/network/settle/:batch` applies the per-entry legs and posts the
  **settlement sweep** (external direct-deposit cash → `Bank` via the Ledger
  port; per-entry posts are `Payable`/`Payable` reclasses) → NSF entries are
  `rejected`; settled entries can be **returned** via `POST /aft/network/returns`
  (a returns file that reverses them).
- **Inbound:** `POST /aft/network/inbound-batch` ingests a CPA-005 file targeting
  nano-bank customers (credits `accept_inbound`; external PAD debits `hold`+
  `release`, or `rejected` on NSF).
- **Auth planes:** customer (`/aft/mandates`, `/aft/credits`, `/aft/debits`,
  `GET /aft/entries`); service-token **network/admin** (`/aft/batches`,
  `/aft/batches/:id/submit`, `/aft/network/settle|inbound-batch|returns`) — the
  shared outbound batch is a bank-operational concept, so listing and cutting it
  are bank ops, not customer ops. Driven by `testing/aft/aft_simulator.py` (plays
  the bank cutoff + ACSS). Customers see their own activity via `/aft/entries`.
  The viewer has an AFT tab.
- **available_balance:** recomputed on **customer** accounts around rail posts;
  the AFT system accounts stay at 0 (float on the $1T overdraft) — same rule as
  Interac.
- **v1 simplifications:** PAD payers are intra-bank (mandate `payer_account_id`
  is a nano-bank account); returns match a settled entry by amount +
  counterparty account; CPA-005 is authentic in shape, not byte-exact.

## Lynx wire rail

The third external rail (`handlers/lynx.rs`, `rails/lynx.rs`, `lynx/iso20022.rs`)
— Canada's **RTGS** system for high-value wires (Lynx). Unlike Interac (retail
push) and AFT (deferred-net batch), each wire settles **individually, in real
time, with settlement finality**. Design spec:
`docs/specs/2026-07-06-lynx-wire-rail-design.md`.

- **Own system accounts (decoupled):** `lynx@nano.bank` owns `LYNX_CLEARING`
  (chequing) + `LYNX_SETTLEMENT` (savings), $1T overdraft. Money moves through
  the same `Rail` verbs, plus an inherent `clawback` for inbound recalls.
- **Two-step settlement:** a customer `POST /lynx/wires` reserves funds in
  `LYNX_CLEARING` and emits a **pacs.008** (`status='sent'`); a network
  `POST /lynx/network/wires/:id/settle` moves `CLEARING → SETTLEMENT` and marks
  the wire `settled` — **final**. A guarded `sent→settled` transition makes a
  concurrent double-settle 409.
- **Finality + recall (no reversal):** a settled wire is irrevocable. A sender
  may `POST /lynx/wires/:id/recall` (emits **camt.056**); the beneficiary side
  answers via `POST /lynx/network/recalls/:id/resolve` (**camt.029** —
  `accept`→refund / `reject`). Symmetrically, an external sender can recall a
  wire we received (`POST /lynx/network/inbound-recall`): we `accept`→claw back
  from the beneficiary (reject if the funds are already spent — v1) or `reject`.
- **GL distinction (RTGS vs Interac/AFT):** settle posts **Dr Payable / Cr
  Bank** (money leaves the bank); inbound posts **Dr Bank / Cr Payable** — real
  central-bank money arrives immediately, where Interac/AFT inbound is a
  `Receivable` until ACSS settles. Send/refund are net-zero `Payable/Payable`.
- **ISO 20022:** `lynx/iso20022.rs` encodes/decodes pacs.008/pacs.009/camt.056/
  camt.029 — authentic-shape, round-trippable, unit-tested (not XSD-validated);
  every exchanged message is stored in the `lynx_messages` outbox.
- **High-value floor:** a configurable minimum (`NANO_BANK__LYNX__MIN_AMOUNT`,
  default $10,000), no ceiling; wires bypass the retail `account_limits`.
- **Auth planes:** customer (`/wires`, `/wires/:id/recall`), service-token
  **network** (`/network/*`, driven by `testing/lynx/lynx_simulator.py`),
  service-token **admin** (`/admin/reject-stale` sweeps unsettled wires). The
  viewer has a Lynx tab.
- **available_balance:** recomputed on **customer** accounts around rail posts;
  the Lynx system accounts stay at 0 (float on the $1T overdraft) — same rule as
  Interac/AFT.

## Interest / NIM engine

The finance engine (`handlers/finance.rs`, `finance/`) turns balances × rates
into GL postings over time (spec #2; design/plan in `docs/specs/` + `docs/plans/`).
Two **service-authenticated** batch endpoints, meant to be cron-driven, plus two
inline recognitions:

- **`POST /api/v1/finance/accrue` `{"as_of":"YYYY-MM-DD"}`** — one day's ACT/365
  interest on all eligible deposit (expense) and credit-card (income) balances;
  writes per-account rows to the `interest_accruals` subledger and posts the
  aggregate GL (`InterestExpense`/`AccruedInterestPayable`,
  `AccruedInterestReceivable`/`InterestIncome`). **Idempotent per date**
  (`accrual_runs`).
- **`POST /api/v1/finance/capitalise` `{"period":"YYYY-MM"}`** — reclasses the
  month's uncapitalised accruals into customer balances (deposit interest
  credited, card interest owed) and charges the **monthly maintenance fee**
  (default $4, waived ≥ $3000). **Idempotent per period** (`capitalisation_runs`).
- **Interchange** is recognized inline at card `capture` (Dr `CashReserves` / Cr
  `InterchangeIncome`, default 150 bps); the **e-transfer fee** inline in
  `send_etransfer` (default $1.50, Dr customer / Cr `FeeIncome`).

Each batch posts a single balanced GL document *before* committing the local
writes, so a core failure rolls back cleanly. Rates are tunable via
`NANO_BANK__FINANCE__*` (`interchange_bps`, `etransfer_fee`, `maintenance_fee`,
`maintenance_waiver`). Every posting is tagged (`product`, `cost_centre`,
`economic_event_id`) on `transactions` / `interest_accruals` for the reporting
specs. Cross-backend smoke: `testing/verify-nim-engine.sh` (run once per
`CORE_BACKEND`).

Sample cron (daily accrual at 02:00; monthly capitalisation on the 1st at 03:00 —
the service token is minted from the network client secret):

```cron
0 2 * * *  curl -fsS -XPOST "$API/api/v1/finance/accrue"     -H "authorization: Bearer $SVC" -H 'content-type: application/json' -d "{\"as_of\":\"$(date +\%F)\"}"
0 3 1 * *  curl -fsS -XPOST "$API/api/v1/finance/capitalise"  -H "authorization: Bearer $SVC" -H 'content-type: application/json' -d "{\"period\":\"$(date -d 'last month' +\%Y-\%m)\"}"
```

## Fraud engine: the FraudCheck port

`api/src/fraud/` mirrors the Ledger pattern: a `FraudCheck` trait with an HTTP
adapter for `nano-bank-fraud-engine` (`:8092`) and a no-op, chosen by
`[fraud] backend` / `NANO_BANK__FRAUD__BACKEND` — default **`off`** (also the
kill switch), so tests and local runs don't need the engine.

- **Call `fraud::gate::screen()` before opening the DB transaction** in any new
  customer- or agent-initiated money path — never hold row locks across the
  network call. It mints the `operation_id`, recovers session context, and maps
  the decision to an outcome.
- The engine (`POST /v1/decisions`) returns an **action, not a score**:
  `allow | block | hold_review | challenge | delay_and_warn` (the last two
  collapse to under-review for now). Declines are opaque to customers
  (`TRANSACTION_DECLINED` / `TRANSACTION_UNDER_REVIEW`).
- A **hold is a success value** (`Screened`), not an `Err`, so a caller can park
  the movement; callers that can't park use `Screened::into_refusal`. Customers
  follow a held movement at `GET /api/v1/reviews/:id`; the bank asks
  `GET /v1/decisions/{operation_id}/disposition` for the case outcome.
- **Failure matrix:** `timeout_ms` (150) + a circuit breaker. Engine
  unreachable → amounts ≤ `fail_closed_above` ($1,000) fail **open** (proceed,
  then `POST /v1/rescore`), larger ones fail **closed** (503). Telemetry
  failures never open the decisions breaker.
- **Agency is spec-derived:** passing agent context sets channel
  `agentic_branch`; never infer it from the auth plane.
- Auditing a refusal is the **caller's** job (the agent plane already records
  it); don't add a second writer in the gate.
- Refused agent actions go to an outbox delivered to `POST /v1/outcomes` by
  `POST /api/v1/fraud/admin/flush-denials` (CronJob
  `k8s/fraud-denial-drainer-cronjob.yaml`). Money rows carry the engine link in
  metadata, exposed at `/api/v1/fraud/admin/transactions/:id/fraud-link` and
  `/admin/rails/:rail/:rail_id/fraud-link`.
- `x-simulated-time` (`fraud/simulated_time.rs`) is honoured only when
  `fraud.accept_simulated_time = true` (default false) — it exists for the world
  model's replays.
- `service_token` must equal the engine's `FRAUD_ENGINE__AUTH__SERVICE_TOKEN`.
  Integration test: `api/tests/fraud_port.rs`.

## CRM integration (`nano-bank-crm`)

- **CRM → bank:** the CRM mints a service token
  (`POST /api/v1/auth/service-token`) and reads `/api/v1/back-office/*`.
  Keep that plane **read-only**; contacts match customers **by email**. The CRM
  runs on `PORT=3100` next to the bank UI on 3000.
- **COO → CRM:** `operations/crm_client.py` + the
  `execute_provision_crm_mandate` MCP lever call the CRM's
  `POST /api/agent/provision` (get-or-create contact + one-customer mandate).
  Env: `CRM_BASE_URL`, `CRM_TENANT_SLUG`, `CRM_PROVISIONING_TOKEN` (= the CRM's
  `CO_PROVISIONING_TOKEN`; the config refuses to start without it).
- **Personal manager → CRM** is planned
  (`docs/superpowers/plans/2026-08-30-personal-manager-crm-access.md`): the
  manager only looks up / mints tokens against existing mandates and reaches the
  CRM's generated MCP tools as a second MCP server. It must never call
  provision, and its own MCP tools must never take a customer id or token.

## World model (`nano-bank-world-model`)

Replays labelled synthetic traffic against the live bank
(`world-model realize --bank-url http://localhost:8081 --engine-url
http://localhost:8092`), reconciles the GL (the hard gate), reads fraud links
back from the bank's fraud-admin endpoints, and posts ground truth to the
engine's `/v1/outcomes`. It never asks the engine to decide — decisions stay
the bank's. Changes to the fraud-link endpoints or the simulated-time header
break it.

## Gotchas

- **DB host is `::1`, not `127.0.0.1`** (dead docker-proxy on IPv4).
- No card accounts are seeded by default — only system GL accounts.
  Create a `credit_card` account (status `active`, an `overdraft_limit` as the
  credit limit, `available_balance` = the limit) to exercise the card rails.
- Config is layered: `api/config/default.toml` plus env vars with prefix
  `NANO_BANK` and `__` as the separator (e.g. `NANO_BANK__SERVER__PORT=8082` to
  run a second instance alongside one already holding `:8081`).
- Remaining stub handlers: KYC document upload
  (`POST /customers/kyc/documents`) and `security/*` (sessions, devices, trust).
- Start a core before the API or every posting returns 503.

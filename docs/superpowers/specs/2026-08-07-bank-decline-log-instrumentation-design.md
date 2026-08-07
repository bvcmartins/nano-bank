# Bank-wide decline log — instrumentation (T5)

**Date:** 2026-08-07
**Status:** design approved, spec under review
**Branch:** `agent-coo`

## Problem

The bank persists no record of *declined* activity. `authorize` (cards) and the
money-movement rails return a decline to the caller and then forget it. Two code
comments already flag the gap:

- `handlers/back_office.rs::ops_exceptions` — "declined authorizations and
  NSF-at-authorization are [not captured]".
- `handlers/back_office.rs::ops_cards` — "Approval/decline *rates* are
  intentionally absent — declined [authorizations aren't persisted]".

So the COO can see recorded exceptions (failed txns, reversals, AFT
returns/rejects, wire recalls) and point-in-time open holds, but it cannot
compute **approval / decline / NSF rates** for cards or see **NSF** on the rails.

Fraud declines are a special case: the separate `nano-bank-fraud-engine` owns the
fraud scoring/reasoning and withholds it from the bank; the COO is out of scope
for fraud/AML by design. But the *fact* that the bank declined an authorization
is an operational event the bank itself produces.

## Goal

A single, generic, bank-wide **decline log** that captures every decline path
(cards + rails + risk + validation), and a read surface that lets the COO report
approval/decline/NSF rates — **without** ever exposing fraud reasoning. Risk
declines are recorded but the COO sees them only folded into an opaque `other`
bucket.

Non-goals: changing any decline's behaviour or response; exposing fraud
scores/rules; a new agent lever; the CFO.

## Design

### 1. Data model — `decline_events`

New DDL `src/core/tables/15_decline_events.sql`:

```sql
CREATE TABLE decline_events (
    decline_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel          TEXT NOT NULL,        -- card_authorize | interac_etransfer | aft_debit | lynx_wire | withdrawal | transfer
    reason_code      TEXT NOT NULL,        -- insufficient_credit | insufficient_funds | risk_declined | over_limit | below_floor | inactive_account | invalid_status | amount_exceeds_max
    reason_category  TEXT NOT NULL,        -- nsf | risk | limit | validation | status
    account_id       UUID,                 -- nullable, NO FK (a not-found decline still logs)
    customer_id      UUID,
    amount           NUMERIC(20,2),
    currency         TEXT NOT NULL DEFAULT 'CAD',
    counterparty     TEXT,                 -- merchant / payee / institution
    metadata         JSONB NOT NULL DEFAULT '{}'   -- small, non-fraud context only
);
CREATE INDEX idx_decline_events_occurred   ON decline_events (occurred_at);
CREATE INDEX idx_decline_events_channel    ON decline_events (channel, occurred_at);
CREATE INDEX idx_decline_events_category   ON decline_events (reason_category, occurred_at);
```

No FK on `account_id` (declines can precede account resolution, and the log must
never fail on a dangling reference). `metadata` never carries fraud scores/rules.

### 2. Write path — one best-effort helper

`api/src/handlers/declines.rs`:

- `enum DeclineReason` with `code() -> &str` and `category() -> &str` — the single
  source of the reason→category mapping (e.g. `InsufficientFunds`/`InsufficientCredit`
  → `nsf`; `RiskDeclined` → `risk`; `OverLimit`/`AmountExceedsMax`/`BelowFloor` →
  `limit`; `InactiveAccount`/`InvalidStatus` → `status`; malformed input →
  `validation`).
- `struct DeclineEvent { channel, reason, account_id: Option<Uuid>, customer_id:
  Option<Uuid>, amount: Option<Decimal>, counterparty: Option<String>, metadata:
  serde_json::Value }`.
- `pub async fn record_decline(pool: &PgPool, ev: DeclineEvent)` — inserts one
  row. **Best-effort:** on any DB error it logs `tracing::warn!` and returns; it
  never propagates an error and never changes the caller's response. It takes the
  **pool** (its own connection), not the caller's `&mut tx`, so it is unaffected
  by the NSF branch's `tx.rollback()` and adds no work inside the money
  transaction.

### 3. Instrument the decline sites

One `record_decline(&state.pool, …)` call at each existing decline decision,
placed after the decline is decided and (for NSF branches) **after** `tx.rollback()`:

- `cards.rs::authorize` — `risk_declined` (fraud precheck branch) and
  `insufficient_credit` (NSF-at-auth branch). The two malformed cases
  (`not a credit card`, inactive status) currently return `AppError`; log them as
  `validation`/`status` for completeness.
- `interac.rs::send_etransfer` — `amount_exceeds_max` (limit), insufficient funds
  (nsf).
- `aft.rs` — PAD debit NSF (nsf), amount over CPA field limit (limit).
- `lynx.rs::initiate_wire` — below the high-value floor (limit), insufficient
  funds (nsf).
- `transactions.rs` — withdrawal/transfer `InsufficientFunds` (nsf) and
  `account_limits` violations (limit).

Behaviour and latency of every path are unchanged (best-effort, out-of-band insert).

### 4. Read side — COO-facing, fraud boundary held

**New `GET /api/v1/back-office/ops/declines?window=24h|7d|30d`**
(`AuthenticatedService`, in `back_office.rs`): returns

```json
{ "window": "30d",
  "total_count": 0, "total_amount": "0.00",
  "by_category": { "nsf": {...}, "limit": {...}, "validation": {...},
                   "status": {...}, "other": {...} },
  "by_channel":  { "card_authorize": {...}, "interac_etransfer": {...}, ... } }
```

The handler **folds `reason_category = 'risk'` into `other`** in SQL
(`CASE WHEN reason_category='risk' THEN 'other' ELSE reason_category END`), so the
fraud bucket never leaves the bank. Each `{...}` is `{count, amount}`.

**Extend `ops_cards`** with `approval`, `decline`, and `nsf` **rates** over the
window:

- `card_declines`   = `decline_events` where `channel='card_authorize'` in window.
- `approved_auths`  = `account_holds` rows created in window with `reason LIKE
  'visa_auth:%'`. Holds are **soft-released** (`released_at` set, rows retained —
  verified in `cards.rs`), so this is an accurate approved-authorization count
  regardless of later release/capture.
- `total = approved_auths + card_declines`; `approval_rate = approved/total`,
  `decline_rate = card_declines/total`, `nsf_rate = nsf_declines/total`.
  All rates omitted (null) when `total = 0`.

### 5. operations MCP + COO

- `operations/bank_client.py` — `declines(window)` GET.
- `operations/metrics.py` — `declines_summary(raw)` (pure): rolls up the
  by-category / by-channel counts and amounts the endpoint returns (risk already
  folded to `other` server-side); the rate fields live in `cards_summary`, which
  reads the rates the `ops_cards` endpoint computes. Unit-tested like the rest.
- `operations/mcp_server.py` — a `declines` tool; fold declines into
  `operations_health`.
- `coo/agent.py` `COO_PROMPT` — add that it can report approval/decline/NSF
  rates; the existing fraud-scope guard (`claims.py` phantom concepts) stays, and
  the prompt must not characterize the `other` bucket as fraud.

### 6. Testing

- **Rust** (`api/tests/back_office.rs`, existing harness): a NSF `authorize` and a
  risk-declined `authorize` each insert one `decline_events` row with the right
  `reason_category`; `record_decline` is best-effort (a forced insert failure
  does not change the decline response); `ops/declines` aggregates and folds
  `risk → other` (a seeded risk row appears under `other`, never `risk`);
  `ops_cards` rate arithmetic on a seeded mix.
- **Python** (`operations/tests`): `metrics.declines_summary` shape/rollup;
  `bank_client.declines` via the injected transport.
- **Live smoke:** drive a real NSF authorize + one risk decline against the
  running bank, then read them back through `ops/declines` and a COO turn
  ("what's our card approval rate and NSF rate this week?").

### 7. Rollout

- Add `15_decline_events.sql` to `k8s/init-db-job.yaml`.
- Apply to the running DB via `kubectl exec deploy/postgres` (as with
  `14_agent_action_ledger.sql`).
- Rebuild/redeploy bank-api + operations-mcp + coo (repo-root build for coo).

## Isolation / boundaries

- **`decline_events`** is one table with no behavioural coupling — writers only
  append, readers only aggregate.
- **`record_decline`** is the single write seam: one helper, best-effort, pool-
  based, so instrumenting a site is a one-line, side-effect-free addition.
- **Fraud boundary** is enforced in one place (the `risk → other` fold in the
  `ops/declines` SQL) plus the unchanged `claims.py` guard, so the COO can never
  see or attribute fraud.
- The read surface reuses the existing back-office → operations-MCP → COO chain;
  no new service or port.

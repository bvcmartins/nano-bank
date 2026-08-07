# Bank-wide Decline Log Instrumentation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every bank decline (cards + rails + risk + validation) in one append-only log and expose approval/decline/NSF rates to the COO, without ever surfacing fraud reasoning.

**Architecture:** One new table `decline_events`; one best-effort, pool-based `record_decline` helper called at each existing decline site (never inside the money transaction, so it survives NSF rollbacks and can't change a decline's outcome); a new `ops/declines` read endpoint that folds `risk → other` in SQL; approval/decline/NSF rates added to `ops_cards` using retained soft-released holds as the approved-auth denominator; surfaced through the existing operations-MCP → COO chain.

**Tech Stack:** Rust (axum 0.7, sqlx, PostgreSQL 16 in kind), Python (FastMCP operations server, pytest), kubectl/kind.

## Global Constraints

- **DB host is `::1`, not `127.0.0.1`** (dead docker-proxy on IPv4).
- **DDL is applied to the running DB via** `kubectl --context kind-nano-bank -n nano-bank exec -i deploy/postgres -- psql -U nanobank_user -d nano_bank_db` (no host `psql`). Snap env first: `export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share`.
- **Never expose fraud data to the COO.** Risk declines are logged but `reason_category='risk'` is folded to `other` in the `ops/declines` SQL; no fraud score/rule/reason is ever put in `decline_events.metadata` or any COO-facing field.
- **`record_decline` is best-effort:** it logs a warning and swallows any error; it must never change a decline's response or latency, and never runs inside the caller's money transaction.
- **Pin `mcp>=1.2,<2`** in any Python requirements touched (2.x drops `mcp.server.fastmcp`).
- **Rust format:** run `rustfmt <file>` only on files you touch — never whole-crate `cargo fmt`.
- **Stop the API by PID**, never `pkill -f target/debug/nano-bank-api`.
- Branch: `agent-coo`.

---

### Task 1: `decline_events` table

**Files:**
- Create: `src/core/tables/15_decline_events.sql`
- Modify: `k8s/init-db-job.yaml` (add a `psql -f …/15_decline_events.sql` line after the `14_agent_action_ledger.sql` line)

**Interfaces:**
- Produces: table `decline_events(decline_id, occurred_at, channel, reason_code, reason_category, account_id, customer_id, amount, currency, counterparty, metadata)` with indexes on `occurred_at`, `(channel, occurred_at)`, `(reason_category, occurred_at)`.

- [ ] **Step 1: Write the DDL**

`src/core/tables/15_decline_events.sql`:

```sql
-- Bank-wide decline log: every declined authorization / money movement, across
-- cards and rails. Append-only; readers only aggregate. NEVER stores fraud
-- scores/rules — only the operational fact of a decline. reason_category is the
-- reporting bucket; the COO read surface folds 'risk' into 'other'.
CREATE TABLE IF NOT EXISTS decline_events (
    decline_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel          TEXT NOT NULL,
    reason_code      TEXT NOT NULL,
    reason_category  TEXT NOT NULL,
    account_id       UUID,
    customer_id      UUID,
    amount           NUMERIC(20,2),
    currency         TEXT NOT NULL DEFAULT 'CAD',
    counterparty     TEXT,
    metadata         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_decline_events_occurred ON decline_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_decline_events_channel  ON decline_events (channel, occurred_at);
CREATE INDEX IF NOT EXISTS idx_decline_events_category ON decline_events (reason_category, occurred_at);
```

- [ ] **Step 2: Register it in the init-db Job**

In `k8s/init-db-job.yaml`, find the line running `14_agent_action_ledger.sql` and add an identical line for `15_decline_events.sql` immediately after it (same `psql … -f /scripts/15_decline_events.sql` form).

- [ ] **Step 3: Apply to the running DB**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
kubectl --context kind-nano-bank -n nano-bank exec -i deploy/postgres -- \
  psql -U nanobank_user -d nano_bank_db < src/core/tables/15_decline_events.sql
```
Expected: `CREATE TABLE` + three `CREATE INDEX`.

- [ ] **Step 4: Verify the table exists**

```bash
kubectl --context kind-nano-bank -n nano-bank exec -i deploy/postgres -- \
  psql -U nanobank_user -d nano_bank_db -c "\d decline_events"
```
Expected: the 11 columns above, three indexes.

- [ ] **Step 5: Commit**

```bash
git add src/core/tables/15_decline_events.sql k8s/init-db-job.yaml
git commit -m "feat(db): decline_events table — bank-wide decline log"
```

---

### Task 2: `DeclineReason` + `record_decline` helper

**Files:**
- Create: `api/src/handlers/declines.rs`
- Modify: `api/src/handlers/mod.rs` (add `pub mod declines;`)

**Interfaces:**
- Produces:
  - `enum DeclineReason { InsufficientFunds, InsufficientCredit, RiskDeclined, OverLimit, AmountExceedsMax, BelowFloor, InactiveAccount, WrongAccountType }` with `fn code(self) -> &'static str` and `fn category(self) -> &'static str`.
  - `struct DeclineEvent { channel: &'static str, reason: DeclineReason, account_id: Option<Uuid>, customer_id: Option<Uuid>, amount: Option<Decimal>, counterparty: Option<String>, metadata: serde_json::Value }`.
  - `async fn record_decline(pool: &sqlx::PgPool, ev: DeclineEvent)` — best-effort insert.

- [ ] **Step 1: Write the failing unit test**

Create `api/src/handlers/declines.rs` with just the test module at the bottom (types added in Step 3):

```rust
#[cfg(test)]
mod tests {
    use super::DeclineReason::*;
    #[test]
    fn categories_map_reasons_to_buckets() {
        assert_eq!(InsufficientCredit.category(), "nsf");
        assert_eq!(InsufficientFunds.category(), "nsf");
        assert_eq!(RiskDeclined.category(), "risk");
        assert_eq!(BelowFloor.category(), "limit");
        assert_eq!(AmountExceedsMax.category(), "limit");
        assert_eq!(OverLimit.category(), "limit");
        assert_eq!(InactiveAccount.category(), "status");
        assert_eq!(WrongAccountType.category(), "validation");
    }
    #[test]
    fn codes_are_stable_strings() {
        assert_eq!(InsufficientCredit.code(), "insufficient_credit");
        assert_eq!(RiskDeclined.code(), "risk_declined");
    }
}
```

Add `pub mod declines;` to `api/src/handlers/mod.rs` (alphabetical, near `pub mod customers;`).

- [ ] **Step 2: Run it to verify it fails to compile**

Run: `cd api && cargo test declines::tests 2>&1 | tail -20`
Expected: compile error — `DeclineReason` not found.

- [ ] **Step 3: Write the implementation** (prepend above the test module)

```rust
//! The bank-wide decline log writer. One best-effort helper, called at every
//! decline site (cards + rails). It writes via the pool (its own connection),
//! never the caller's money transaction, so it survives an NSF-branch rollback
//! and adds no work inside that transaction. A write failure is logged and
//! swallowed — instrumentation must never change a decline's outcome. The log
//! NEVER stores fraud scores/rules, only the operational fact of a decline.
use rust_decimal::Decimal;
use serde_json::Value;
use sqlx::PgPool;
use uuid::Uuid;

#[derive(Debug, Clone, Copy)]
pub enum DeclineReason {
    InsufficientFunds,
    InsufficientCredit,
    RiskDeclined,
    OverLimit,
    AmountExceedsMax,
    BelowFloor,
    InactiveAccount,
    WrongAccountType,
}

impl DeclineReason {
    pub fn code(self) -> &'static str {
        match self {
            DeclineReason::InsufficientFunds => "insufficient_funds",
            DeclineReason::InsufficientCredit => "insufficient_credit",
            DeclineReason::RiskDeclined => "risk_declined",
            DeclineReason::OverLimit => "over_limit",
            DeclineReason::AmountExceedsMax => "amount_exceeds_max",
            DeclineReason::BelowFloor => "below_floor",
            DeclineReason::InactiveAccount => "inactive_account",
            DeclineReason::WrongAccountType => "wrong_account_type",
        }
    }
    pub fn category(self) -> &'static str {
        match self {
            DeclineReason::InsufficientFunds | DeclineReason::InsufficientCredit => "nsf",
            DeclineReason::RiskDeclined => "risk",
            DeclineReason::OverLimit
            | DeclineReason::AmountExceedsMax
            | DeclineReason::BelowFloor => "limit",
            DeclineReason::InactiveAccount => "status",
            DeclineReason::WrongAccountType => "validation",
        }
    }
}

pub struct DeclineEvent {
    pub channel: &'static str,
    pub reason: DeclineReason,
    pub account_id: Option<Uuid>,
    pub customer_id: Option<Uuid>,
    pub amount: Option<Decimal>,
    pub counterparty: Option<String>,
    pub metadata: Value,
}

/// Append one decline to `decline_events`. Best-effort: errors are logged and
/// swallowed. `metadata` is passed as text and cast to jsonb server-side.
pub async fn record_decline(pool: &PgPool, ev: DeclineEvent) {
    let res = sqlx::query(
        "INSERT INTO decline_events \
         (channel, reason_code, reason_category, account_id, customer_id, amount, counterparty, metadata) \
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
    )
    .bind(ev.channel)
    .bind(ev.reason.code())
    .bind(ev.reason.category())
    .bind(ev.account_id)
    .bind(ev.customer_id)
    .bind(ev.amount)
    .bind(ev.counterparty)
    .bind(ev.metadata.to_string())
    .execute(pool)
    .await;
    if let Err(e) = res {
        tracing::warn!(error = %e, channel = ev.channel, reason = ev.reason.code(),
                       "failed to record decline (best-effort, ignored)");
    }
}
```

- [ ] **Step 4: Run the unit tests + a type check**

Run: `cd api && cargo test declines::tests 2>&1 | tail -20 && cargo check 2>&1 | tail -3`
Expected: 2 tests pass; `cargo check` clean.

- [ ] **Step 5: Commit**

```bash
git add api/src/handlers/declines.rs api/src/handlers/mod.rs
git commit -m "feat(api): DeclineReason + best-effort record_decline helper"
```

---

### Task 3: `ops/declines` endpoint + `ops_cards` rates

**Files:**
- Modify: `api/src/handlers/back_office.rs` (add `ops_declines`, route it, add rate fields to `ops_cards`)
- Modify: `api/tests/back_office_ops.rs` (black-box tests, run against the stack in Task 8)

**Interfaces:**
- Consumes: `window_cutoff(&str) -> Result<DateTime<Utc>, AppError>`, `WindowQuery`, `back_office_routes()`, `AuthenticatedService`, `CardsResponse` (all in `back_office.rs`).
- Produces: `GET /api/v1/back-office/ops/declines?window=` → `{window, since, total_count, total_amount, by_category:{...}, by_channel:{...}}`; `ops_cards` response gains a `rates` object `{approved, declined, nsf_declined, approval_rate?, decline_rate?, nsf_rate?}`.

- [ ] **Step 1: Add the `ops/declines` handler + types** (in `back_office.rs`, near the other `ops_*` handlers)

```rust
#[derive(Serialize)]
struct DeclineBucket {
    count: i64,
    amount: Decimal,
}

#[derive(sqlx::FromRow)]
struct DeclineGroupRow {
    key: String,
    count: i64,
    amount: Decimal,
}

#[derive(Serialize)]
struct DeclinesResponse {
    window: String,
    since: DateTime<Utc>,
    total_count: i64,
    total_amount: Decimal,
    by_category: std::collections::BTreeMap<String, DeclineBucket>,
    by_channel: std::collections::BTreeMap<String, DeclineBucket>,
}

fn declines_map(rows: Vec<DeclineGroupRow>) -> (std::collections::BTreeMap<String, DeclineBucket>, i64, Decimal) {
    let mut m = std::collections::BTreeMap::new();
    let (mut tc, mut ta) = (0i64, Decimal::ZERO);
    for r in rows {
        tc += r.count;
        ta += r.amount;
        m.insert(r.key, DeclineBucket { count: r.count, amount: r.amount });
    }
    (m, tc, ta)
}

/// Declines over the window, grouped by category and by channel. The fraud
/// bucket (`reason_category='risk'`) is folded into `other` in SQL, so no fraud
/// signal ever leaves the bank.
async fn ops_declines(
    _: AuthenticatedService,
    State(state): State<AppState>,
    Query(q): Query<WindowQuery>,
) -> Result<Json<DeclinesResponse>, AppError> {
    let window = q.window.unwrap_or_else(|| "24h".to_string());
    let since = window_cutoff(&window)?;

    let by_category = sqlx::query_as::<_, DeclineGroupRow>(
        "SELECT CASE WHEN reason_category='risk' THEN 'other' ELSE reason_category END AS key,
                COUNT(*) AS count, COALESCE(SUM(amount),0) AS amount
         FROM decline_events WHERE occurred_at >= $1
         GROUP BY 1 ORDER BY 1",
    )
    .bind(since)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let by_channel = sqlx::query_as::<_, DeclineGroupRow>(
        "SELECT channel AS key, COUNT(*) AS count, COALESCE(SUM(amount),0) AS amount
         FROM decline_events WHERE occurred_at >= $1
         GROUP BY channel ORDER BY channel",
    )
    .bind(since)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let (by_category, total_count, total_amount) = declines_map(by_category);
    let (by_channel, _, _) = declines_map(by_channel);
    Ok(Json(DeclinesResponse { window, since, total_count, total_amount, by_category, by_channel }))
}
```

- [ ] **Step 2: Route it** — in `back_office_routes()`, after the `ops_cards` route line:

```rust
        .route("/ops/declines", get(ops_declines))
```

- [ ] **Step 3: Add rates to `ops_cards`**

Add the struct (near `CardsResponse`):

```rust
#[derive(Serialize)]
struct CardAuthRates {
    approved: i64,
    declined: i64,
    nsf_declined: i64,
    approval_rate: Option<f64>,
    decline_rate: Option<f64>,
    nsf_rate: Option<f64>,
}
```

Add `rates: CardAuthRates` as a field on `CardsResponse`. In `ops_cards`, before building the response, compute it:

```rust
    // Approved authorizations = holds created in the window (each approved auth
    // inserts one 'visa_auth:%' hold; holds are soft-released, rows retained).
    let approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM account_holds WHERE reason LIKE 'visa_auth:%' AND created_at >= $1",
    )
    .bind(since)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;
    let declined: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM decline_events WHERE channel='card_authorize' AND occurred_at >= $1",
    )
    .bind(since)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;
    let nsf_declined: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM decline_events \
         WHERE channel='card_authorize' AND reason_category='nsf' AND occurred_at >= $1",
    )
    .bind(since)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;
    let total = approved + declined;
    let rate = |n: i64| if total > 0 { Some(n as f64 / total as f64) } else { None };
    let rates = CardAuthRates {
        approved,
        declined,
        nsf_declined,
        approval_rate: rate(approved),
        decline_rate: rate(declined),
        nsf_rate: rate(nsf_declined),
    };
```

Add `rates` to the `CardsResponse { … }` constructor at the end of `ops_cards`. Update the stale doc-comment on `ops_cards` (drop "Approval/decline rates are intentionally absent"; say rates are now computed from `decline_events` + retained holds). Update the `ops_exceptions` doc-comment that says declines aren't captured.

- [ ] **Step 4: Write the black-box tests** (append to `api/tests/back_office_ops.rs`; they guard on `stack_up` and run in Task 8)

```rust
#[tokio::test]
async fn declines_returns_bucketed_shape_for_a_service_token() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let svc = service_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/declines?window=30d", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let body: serde_json::Value = r.json().await.unwrap();
    assert!(body.get("by_category").is_some());
    assert!(body.get("by_channel").is_some());
    // The fraud bucket must never surface: 'risk' is folded to 'other'.
    assert!(body["by_category"].get("risk").is_none());
}

#[tokio::test]
async fn declines_rejects_a_customer_token() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let cust = customer_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/declines?window=30d", base_url()))
        .bearer_auth(&cust)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 401);
}

#[tokio::test]
async fn cards_summary_now_carries_auth_rates() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let svc = service_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/cards?window=30d", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let body: serde_json::Value = r.json().await.unwrap();
    assert!(body["rates"].get("approved").is_some());
    assert!(body["rates"].get("decline_rate").is_some());
}
```

- [ ] **Step 5: Type-check + commit**

Run: `cd api && cargo check 2>&1 | tail -3`
Expected: clean.

```bash
rustfmt api/src/handlers/back_office.rs
git add api/src/handlers/back_office.rs api/tests/back_office_ops.rs
git commit -m "feat(api): ops/declines endpoint (risk->other) + ops_cards auth rates"
```

---

### Task 4: Instrument card `authorize`

**Files:**
- Modify: `api/src/handlers/cards.rs` (`authorize`, the two decline branches)

**Interfaces:**
- Consumes: `record_decline`, `DeclineEvent`, `DeclineReason` from Task 2.

- [ ] **Step 1: Import the helper** — near the top of `cards.rs`:

```rust
use crate::handlers::declines::{record_decline, DeclineEvent, DeclineReason};
```

- [ ] **Step 2: Log the risk decline** — in `authorize`, in the `Err(AppError::TransactionDeclined) | Err(AppError::TransactionUnderReview(_))` arm, immediately BEFORE the `return Ok((StatusCode::OK, Json(AuthorizeResponse { … reason: Some("risk_declined") … })))`:

```rust
                record_decline(
                    &state.pool,
                    DeclineEvent {
                        channel: "card_authorize",
                        reason: DeclineReason::RiskDeclined,
                        account_id: Some(req.account_id),
                        customer_id: Some(owner),
                        amount: Some(amount),
                        counterparty: Some(merchant.clone()),
                        metadata: serde_json::json!({}),
                    },
                )
                .await;
```

(`owner` is bound in the `if let Some((owner, available_balance)) = precheck` arm; `merchant` is `String`, so clone it since the response still moves it.)

- [ ] **Step 3: Log the insufficient-credit (NSF) decline** — in the `if amount > card.available_balance {` branch, AFTER `tx.rollback().await?;` and BEFORE the `return Ok((StatusCode::OK, Json(AuthorizeResponse { … reason: Some("insufficient_credit") … })))`:

```rust
        record_decline(
            &state.pool,
            DeclineEvent {
                channel: "card_authorize",
                reason: DeclineReason::InsufficientCredit,
                account_id: Some(card.account_id),
                customer_id: Some(card.customer_id),
                amount: Some(amount),
                counterparty: Some(merchant.clone()),
                metadata: serde_json::json!({ "available": card.available_balance.to_string() }),
            },
        )
        .await;
```

- [ ] **Step 4: Type-check**

Run: `cd api && cargo check 2>&1 | tail -3`
Expected: clean (if `merchant` was moved before the log, adjust the two `Some(merchant.clone())` / final `merchant` so the log clones and the response takes the original).

- [ ] **Step 5: Commit**

```bash
rustfmt api/src/handlers/cards.rs
git add api/src/handlers/cards.rs
git commit -m "feat(api): log card-authorize risk + NSF declines to the decline log"
```

---

### Task 5: Instrument the rails + transactions

**Files:**
- Modify: `api/src/handlers/interac.rs`, `api/src/handlers/aft.rs`, `api/src/handlers/lynx.rs`, `api/src/handlers/transactions.rs`

**Interfaces:**
- Consumes: `record_decline`, `DeclineEvent`, `DeclineReason` (Task 2).

Canonical call shape (place at each existing decline branch, using the account /
customer / amount / counterparty already in scope there; write via `&state.pool`,
after any `tx.rollback()`):

```rust
record_decline(
    &state.pool,
    DeclineEvent {
        channel: "<channel>",
        reason: DeclineReason::<Reason>,
        account_id: Some(<account_id_in_scope>),   // or None if not yet resolved
        customer_id: Some(<customer_id_in_scope>), // or None
        amount: Some(<amount_in_scope>),
        counterparty: <Some(handle/payee/institution) or None>,
        metadata: serde_json::json!({}),
    },
)
.await;
```

- [ ] **Step 1: Add the import to each of the four handler files**

```rust
use crate::handlers::declines::{record_decline, DeclineEvent, DeclineReason};
```

- [ ] **Step 2: Instrument each site.** Grep each file for its decline branch and add the call:

| File | Decline branch (grep) | `channel` | `reason` |
|---|---|---|---|
| `interac.rs` | `amount > max_amount(&state)` → `BadRequest("amount exceeds per-transfer max…")` | `interac_etransfer` | `AmountExceedsMax` |
| `interac.rs` | `if amount > sender.available_balance { … InsufficientFunds }` | `interac_etransfer` | `InsufficientFunds` |
| `aft.rs` | `amount > max_cpa_amount()` → `BadRequest("amount exceeds AFT file field limit")` | `aft_debit` | `AmountExceedsMax` |
| `aft.rs` | the PAD-debit NSF branch (insufficient funds on the payer account) | `aft_debit` | `InsufficientFunds` |
| `lynx.rs` | `amount < floor` → `BadRequest("amount below the high-value floor…")` | `lynx_wire` | `BelowFloor` |
| `lynx.rs` | the wire NSF branch (`InsufficientFunds`, if present on send) | `lynx_wire` | `InsufficientFunds` |
| `transactions.rs` | withdrawal/transfer `InsufficientFunds` | `withdrawal` / `transfer` | `InsufficientFunds` |
| `transactions.rs` | `account_limits` violation (daily/amount cap) | `withdrawal` / `transfer` | `OverLimit` |

For a branch that returns `Err(AppError::…)` (not a 200 decline), place the
`record_decline(...).await;` immediately before the `return Err(...)` / `?` that
produces the decline, after any `tx.rollback().await?;`. Where the amount check
happens before a tx opens (e.g. Interac max, Lynx floor), no rollback is needed —
just log then return. Use `None` for `account_id`/`customer_id` only if they are
genuinely not yet resolved at that point.

- [ ] **Step 3: Type-check**

Run: `cd api && cargo check 2>&1 | tail -3`
Expected: clean. Fix any move/borrow errors by cloning the counterparty string for the log.

- [ ] **Step 4: Commit**

```bash
rustfmt api/src/handlers/interac.rs api/src/handlers/aft.rs api/src/handlers/lynx.rs api/src/handlers/transactions.rs
git add api/src/handlers/interac.rs api/src/handlers/aft.rs api/src/handlers/lynx.rs api/src/handlers/transactions.rs
git commit -m "feat(api): log NSF/limit/floor declines across interac, aft, lynx, transactions"
```

---

### Task 6: operations MCP + metrics (Python)

**Files:**
- Modify: `operations/bank_client.py` (add `declines`)
- Modify: `operations/metrics.py` (add `declines_summary`; thread `rates` through `cards_summary`)
- Modify: `operations/mcp_server.py` (add `declines` tool; fold declines into `operations_health`)
- Modify: `operations/tests/test_metrics.py`, `operations/tests/test_bank_client.py`

**Interfaces:**
- Consumes: `GET /api/v1/back-office/ops/declines` (Task 3); `ops/cards` now includes `rates` (Task 3).
- Produces: `BankClient.declines(window)`, `metrics.declines_summary(raw)`, `metrics.cards_summary(...)` now includes `rates`, MCP tool `declines`.

- [ ] **Step 1: Write the failing metrics test** — append to `operations/tests/test_metrics.py`:

```python
def test_declines_summary_rolls_up_categories_and_channels():
    raw = {
        "window": "30d", "total_count": 3, "total_amount": "1500.00",
        "by_category": {"nsf": {"count": 2, "amount": "1000.00"},
                        "other": {"count": 1, "amount": "500.00"}},
        "by_channel": {"card_authorize": {"count": 3, "amount": "1500.00"}},
    }
    out = metrics.declines_summary(raw)
    assert out["total_count"] == 3
    assert out["by_category"]["nsf"]["count"] == 2
    assert out["by_channel"]["card_authorize"]["count"] == 3
    # the fraud bucket must never appear
    assert "risk" not in out["by_category"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd operations && python -m pytest tests/test_metrics.py::test_declines_summary_rolls_up_categories_and_channels -q`
Expected: FAIL — `metrics.declines_summary` not defined.

- [ ] **Step 3: Implement `declines_summary`** — in `operations/metrics.py`:

```python
def declines_summary(raw: dict) -> dict:
    """Pass through the decline rollup from ops/declines (already aggregated and
    risk-folded server-side). Defensive: drop a 'risk' bucket if one ever appears
    so the COO can never see the fraud category."""
    by_category = {k: v for k, v in (raw.get("by_category") or {}).items() if k != "risk"}
    return {
        "window": raw.get("window"),
        "total_count": raw.get("total_count", 0),
        "total_amount": raw.get("total_amount", "0"),
        "by_category": by_category,
        "by_channel": raw.get("by_channel") or {},
    }
```

- [ ] **Step 4: Thread `rates` through `cards_summary`** — in `metrics.cards_summary`, add `"rates": raw.get("rates")` to the returned dict (leave the existing fields as-is).

- [ ] **Step 5: Add the bank-client method** — in `operations/bank_client.py`, beside `cards`:

```python
    def declines(self, window: str = "24h") -> dict:
        return self._get("/api/v1/back-office/ops/declines", {"window": window})
```

- [ ] **Step 6: Add the MCP tool + fold into health** — in `operations/mcp_server.py`:

```python
    @mcp.tool()
    def declines(window: str = "24h") -> dict:
        """Declined activity over a window (24h|7d|30d): counts + amounts by
        category (nsf/limit/validation/status/other) and by channel. 'other' is a
        catch-all — do NOT characterize it as fraud (fraud data is out of scope)."""
        return _stringify(metrics.declines_summary(bank.declines(window)))
```

In `operations_health`, add `"declines": metrics.declines_summary(bank.declines(window))` to the bundle dict.

- [ ] **Step 7: Run the tests**

Run: `cd operations && python -m pytest -q 2>&1 | tail -5`
Expected: all pass (existing + the new one).

- [ ] **Step 8: Commit**

```bash
git add operations/bank_client.py operations/metrics.py operations/mcp_server.py operations/tests/
git commit -m "feat(operations): declines tool + metrics + cards auth rates"
```

---

### Task 7: COO prompt — enable rate reporting, keep fraud out

**Files:**
- Modify: `coo/agent.py` (`COO_PROMPT`)

- [ ] **Step 1: Extend the prompt** — add this sentence to `COO_PROMPT` (after the sentence about naming the window), keeping every existing instruction:

```
"You can now report card approval, decline and NSF rates and the decline "
"breakdown (via the `declines` tool). The 'other' decline bucket is a catch-all "
"— never call it fraud or attribute it to fraud/AML; that data is out of your "
"scope. "
```

- [ ] **Step 2: Run the COO offline tests** (ensure nothing regressed)

Run: `cd coo && python -m pytest -q 2>&1 | tail -5`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add coo/agent.py
git commit -m "feat(coo): report approval/decline/NSF rates; keep 'other' non-fraud"
```

---

### Task 8: Deploy + live verification

**Files:** none (build/deploy/verify only)

- [ ] **Step 1: Rebuild + reload the three images**

```bash
export XDG_RUNTIME_DIR=/run/user/1000 XDG_DATA_HOME=/home/bmartins/.local/share
cd /home/bmartins/dev/nano-bank
docker build -t nano-bank-api:dev api
docker build -t nano-operations-mcp:dev operations
docker build -f coo/Dockerfile -t nano-coo:dev .
kind load docker-image nano-bank-api:dev nano-operations-mcp:dev nano-coo:dev --name nano-bank
kubectl --context kind-nano-bank -n nano-bank rollout restart deploy/bank-api deploy/operations-mcp deploy/coo
kubectl --context kind-nano-bank -n nano-bank rollout status deploy/bank-api --timeout=200s
kubectl --context kind-nano-bank -n nano-bank rollout status deploy/operations-mcp --timeout=120s
kubectl --context kind-nano-bank -n nano-bank rollout status deploy/coo --timeout=180s
```

- [ ] **Step 2: Re-establish the bank-api port-forward** (pod restarted)

```bash
pkill -f "port-forward.*svc/bank-api" 2>/dev/null; sleep 1
kubectl --context kind-nano-bank -n nano-bank port-forward svc/bank-api 8081:8081 &
sleep 3; curl -s http://localhost:8081/health | head -c 200; echo
```

- [ ] **Step 3: Run the black-box integration tests against the stack**

Run: `cd api && cargo test --test back_office_ops 2>&1 | tail -20`
Expected: the three new tests (`declines_returns_bucketed_shape_for_a_service_token`, `declines_rejects_a_customer_token`, `cards_summary_now_carries_auth_rates`) PASS (not skipped).

- [ ] **Step 4: Drive a real card decline, read it back**

```bash
# service token
SVC=$(curl -s -X POST http://localhost:8081/api/v1/auth/service-token \
  -H 'content-type: application/json' \
  -d '{"client_secret":"nano-bank-visa-network-secret-change-me"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
# create a customer + credit_card account (reuse demos/05-coo/seed_open_aft.py style),
# then authorize a huge amount to force an insufficient_credit decline:
#   POST /api/v1/cards/authorize {account_id, amount: 1000000, merchant:"Smoke"} (service auth)
# then read:
curl -s "http://localhost:8081/api/v1/back-office/ops/declines?window=24h" -H "authorization: Bearer $SVC" | python3 -m json.tool
curl -s "http://localhost:8081/api/v1/back-office/ops/cards?window=24h" -H "authorization: Bearer $SVC" | python3 -c "import sys,json;print(json.load(sys.stdin)['rates'])"
```
Expected: `ops/declines` shows `by_channel.card_authorize` and `by_category.nsf` ≥ 1; `ops/cards` `rates` shows `declined ≥ 1` and a non-null `decline_rate`. Confirm **no `risk` key** in `by_category`.

- [ ] **Step 5: Confirm the decline row directly + verify the fraud fold**

```bash
kubectl --context kind-nano-bank -n nano-bank exec -i deploy/postgres -- \
  psql -U nanobank_user -d nano_bank_db -c \
  "SELECT channel, reason_code, reason_category, amount FROM decline_events ORDER BY occurred_at DESC LIMIT 5;"
```
Expected: the `card_authorize / insufficient_credit / nsf` row present. (If you also drive a risk decline, confirm it stores `risk` in the table but the `ops/declines` response shows it only under `other`.)

- [ ] **Step 6: COO turn (optional, end-to-end)**

Re-forward COO (`svc/coo 8093`) and ask: *"What's our card approval rate and NSF rate this week, and the decline breakdown?"* Expected: grounded rates from the `declines`/`cards` tools; the `other` bucket is never called fraud.

- [ ] **Step 7: Final commit / push**

```bash
git push origin agent-coo
```

---

## Self-review notes

- **Spec coverage:** table (T1), helper (T2), endpoint + fold + rates (T3), instrumentation across all channels (T4–T5), MCP/metrics/COO surfacing (T6–T7), tests + rollout (T3 tests, T8). All spec sections mapped.
- **Fraud boundary** enforced in two places, both covered: the SQL fold (T3, tested in T8 Step 4/5) and the defensive Python drop (T6 test).
- **Denominator** resolved: approved auths from retained `visa_auth:%` holds (T3), no approval logging needed.

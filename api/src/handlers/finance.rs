//! Interest / NIM engine batch endpoints (spec #2). System-authenticated; driven
//! by cron. `/accrue` computes one day's interest across all eligible accounts and
//! posts the aggregate GL effect; per-account detail lands in `interest_accruals`.
//! `/capitalise` reclasses a month's accruals into customer balances and charges
//! the monthly maintenance fee.
//!
//! Each batch posts a **single, balanced GL document** (built from the run's
//! totals) *before* committing the local subledger writes, so a core failure
//! rolls the batch back cleanly and never leaves the GL and subledger drifting.
use axum::{extract::State, routing::post, Json, Router};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::errors::AppError;
use crate::finance::{daily_interest, maintenance_due};
use crate::handlers::cards::{post_two_legged, reference_number, Tx};
use crate::handlers::AppState;
use crate::ledger::{Account as Gl, Direction, EntryLine, NewEntry};
use crate::middleware::auth::AuthenticatedService;

pub fn finance_routes() -> Router<AppState> {
    Router::new()
        .route("/accrue", post(accrue))
        .route("/capitalise", post(capitalise))
}

/// Post one balanced GL document with the given lines (skipping when empty).
/// A core failure is surfaced as 503 so the caller can roll back and re-drive.
async fn post_balanced(
    state: &AppState,
    reference: &str,
    description: &str,
    lines: Vec<EntryLine>,
) -> Result<(), AppError> {
    if lines.is_empty() {
        return Ok(());
    }
    state
        .ledger
        .post_entry(NewEntry {
            reference: Some(reference.to_string()),
            description: Some(description.to_string()),
            lines,
        })
        .await
        .map_err(|e| AppError::ServiceUnavailable(format!("GL core post failed: {e}")))?;
    Ok(())
}

fn line(account: Gl, direction: Direction, amount: Decimal) -> EntryLine {
    EntryLine { account, direction, amount }
}

/// The single "is this a **real customer** account?" predicate, shared by every
/// finance query that must skip the bank's own synthetic accounts. Those are the
/// `@nano.bank` system customers (`cash`, `system`, `interac`, `aft`, `lynx`) that
/// own the clearing/settlement floats; a real customer never has a `@nano.bank`
/// email. Assumes the query joins `accounts a` to `customers c`.
///
/// This replaces the two implicit heuristics the accrual and fee queries used to
/// lean on (`interest_rate > 0` for accrual, `overdraft_limit < 1000000` for the
/// maintenance fee) — a real customer with a large overdraft is no longer
/// silently exempt, and a system account with a stray nonzero rate is no longer
/// accrued.
const CUSTOMER_ACCOUNT: &str = "c.email NOT LIKE '%@nano.bank'";

/// Drop a customer account's `available_balance` to 0 before posting a leg that
/// lowers `balance`, so `chk_available_balance_logical` (available <= balance +
/// overdraft) can't trip mid-statement. Recompute the true value afterwards.
/// Same guard the rails use around their posts.
async fn zero_available(tx: &mut Tx<'_>, account_id: Uuid) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE accounts SET available_balance = 0 WHERE account_id = $1")
        .bind(account_id)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

/// Refresh a customer account's available balance (the balance trigger maintains
/// only `balance`). Deposit accounts: balance + overdraft − holds; a credit card:
/// limit − balance − holds (available credit shrinks as the owed balance grows).
async fn recompute_available(tx: &mut Tx<'_>, account_id: Uuid) -> Result<(), sqlx::Error> {
    sqlx::query(
        "UPDATE accounts SET available_balance = CASE \
             WHEN account_type = 'credit_card' \
               THEN overdraft_limit - balance \
               ELSE balance + overdraft_limit END \
           - COALESCE((SELECT sum(amount) FROM account_holds \
                       WHERE account_id = $1 AND released_at IS NULL), 0), \
           updated_at = CURRENT_TIMESTAMP \
         WHERE account_id = $1",
    )
    .bind(account_id)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

/// The synthetic `EXTERNAL_CASH` account id (chequing under `cash@nano.bank`);
/// the customer-level contra for interest credited and fees charged.
async fn external_cash_id(state: &AppState) -> Result<Uuid, AppError> {
    sqlx::query_scalar::<_, Uuid>(
        "SELECT a.account_id FROM accounts a JOIN customers c ON c.customer_id = a.customer_id \
         WHERE c.email = 'cash@nano.bank' AND a.account_type = 'chequing' LIMIT 1",
    )
    .fetch_optional(&state.pool)
    .await?
    .ok_or_else(|| AppError::ServiceUnavailable("external cash account not initialised".to_string()))
}

/// Charge the flat outgoing e-transfer fee to the sender's deposit account and
/// recognize it as fee income (tagged `product=payment`, `cost_centre=payments`).
/// Returns the fee charged (zero if configured off or deferred by the overdraft
/// floor). Call inside the sender's transaction, before it commits.
pub(crate) async fn charge_etransfer_fee(
    state: &AppState,
    tx: &mut Tx<'_>,
    sender_account_id: Uuid,
    sender_customer_id: Uuid,
) -> Result<Decimal, AppError> {
    let fee = state.settings.finance_config().etransfer_fee;
    if fee <= Decimal::ZERO {
        return Ok(Decimal::ZERO);
    }
    // Fee floor: never force the sender past its overdraft limit.
    let (balance, overdraft_limit) = sqlx::query_as::<_, (Decimal, Decimal)>(
        "SELECT balance, overdraft_limit FROM accounts WHERE account_id = $1",
    )
    .bind(sender_account_id)
    .fetch_one(&mut **tx)
    .await?;
    if balance - fee < -overdraft_limit {
        tracing::warn!(%sender_account_id, "e-transfer fee deferred: would breach overdraft limit");
        return Ok(Decimal::ZERO);
    }
    let cash_id = external_cash_id(state).await?;
    let reference = reference_number("FEE");
    let txn_id: Uuid = sqlx::query_scalar(
        "INSERT INTO transactions \
           (reference_number, transaction_type, amount, description, status, initiated_by, \
            completed_at, product, cost_centre, economic_event_id) \
         VALUES ($1,'fee',$2,'Interac e-Transfer fee','completed',$3, \
                 CURRENT_TIMESTAMP,'payment','payments',$4) RETURNING transaction_id",
    )
    .bind(&reference)
    .bind(fee)
    .bind(sender_customer_id)
    .bind(Uuid::new_v4())
    .fetch_one(&mut **tx)
    .await?;
    // Customer debit (balance down); EXTERNAL_CASH credit.
    zero_available(tx, sender_account_id).await?;
    post_two_legged(tx, txn_id, sender_account_id, "debit", cash_id, "credit", fee).await?;
    recompute_available(tx, sender_account_id).await?;
    post_balanced(
        state,
        &reference,
        "Interac e-Transfer fee",
        vec![
            line(Gl::CustomerDeposits, Direction::Debit, fee),
            line(Gl::FeeIncome, Direction::Credit, fee),
        ],
    )
    .await?;
    Ok(fee)
}

// ---------------------------------------------------------------------------
// accrue (daily)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct AccrueRequest {
    as_of: chrono::NaiveDate,
}

#[derive(Debug, Serialize)]
struct AccrueResponse {
    accrual_date: chrono::NaiveDate,
    expense_total: Decimal,
    income_total: Decimal,
    economic_event_id: Uuid,
}

/// Compute one day's interest on every eligible account, write the per-account
/// subledger rows, post the aggregate GL, and record the run. Idempotent per date.
async fn accrue(
    State(state): State<AppState>,
    _svc: AuthenticatedService,
    Json(req): Json<AccrueRequest>,
) -> Result<Json<AccrueResponse>, AppError> {
    if let Some(row) = sqlx::query_as::<_, (Uuid, Decimal, Decimal)>(
        "SELECT economic_event_id, expense_total, income_total FROM accrual_runs \
         WHERE accrual_date = $1 AND status = 'completed'",
    )
    .bind(req.as_of)
    .fetch_optional(&state.pool)
    .await?
    {
        return Ok(Json(AccrueResponse {
            accrual_date: req.as_of,
            economic_event_id: row.0,
            expense_total: row.1,
            income_total: row.2,
        }));
    }

    let event_id = Uuid::new_v4();
    let mut tx = state.pool.begin().await?;

    // Deposit side: liability balances earn interest (an expense to the bank).
    // `CUSTOMER_ACCOUNT` excludes the bank's own system accounts; `interest_rate
    // > 0` / `balance > 0` are now just "would accrue nonzero" filters.
    let deposits = sqlx::query_as::<_, (Uuid, Decimal, Decimal)>(&format!(
        "SELECT a.account_id, a.balance, a.interest_rate FROM accounts a \
         JOIN customers c ON c.customer_id = a.customer_id \
         WHERE a.status = 'active' AND a.balance > 0 AND a.interest_rate > 0 \
           AND a.account_type IN ('chequing','savings') AND {CUSTOMER_ACCOUNT}"
    ))
    .fetch_all(&mut *tx)
    .await?;

    let mut expense_total = Decimal::ZERO;
    for (account_id, balance, rate) in &deposits {
        let amount = daily_interest(*balance, *rate);
        if amount.is_zero() {
            continue;
        }
        expense_total += amount;
        sqlx::query(
            "INSERT INTO interest_accruals \
               (account_id, accrual_date, product, cost_centre, principal, rate, amount, side, economic_event_id) \
             VALUES ($1,$2,'deposit','deposits',$3,$4,$5,'expense',$6) \
             ON CONFLICT (account_id, accrual_date) DO NOTHING",
        )
        .bind(account_id).bind(req.as_of).bind(balance).bind(rate).bind(amount).bind(event_id)
        .execute(&mut *tx).await?;
    }

    // Asset side: credit-card balances the customer owes accrue interest income.
    let cards = sqlx::query_as::<_, (Uuid, Decimal, Decimal)>(&format!(
        "SELECT a.account_id, a.balance, a.interest_rate FROM accounts a \
         JOIN customers c ON c.customer_id = a.customer_id \
         WHERE a.status = 'active' AND a.balance > 0 AND a.interest_rate > 0 \
           AND a.account_type = 'credit_card' AND {CUSTOMER_ACCOUNT}"
    ))
    .fetch_all(&mut *tx)
    .await?;

    let mut income_total = Decimal::ZERO;
    for (account_id, owed, apr) in &cards {
        let amount = daily_interest(*owed, *apr);
        if amount.is_zero() {
            continue;
        }
        income_total += amount;
        sqlx::query(
            "INSERT INTO interest_accruals \
               (account_id, accrual_date, product, cost_centre, principal, rate, amount, side, economic_event_id) \
             VALUES ($1,$2,'card','lending',$3,$4,$5,'income',$6) \
             ON CONFLICT (account_id, accrual_date) DO NOTHING",
        )
        .bind(account_id).bind(req.as_of).bind(owed).bind(apr).bind(amount).bind(event_id)
        .execute(&mut *tx).await?;
    }

    sqlx::query(
        "INSERT INTO accrual_runs (accrual_date, economic_event_id, expense_total, income_total) \
         VALUES ($1,$2,$3,$4)",
    )
    .bind(req.as_of).bind(event_id).bind(expense_total).bind(income_total)
    .execute(&mut *tx).await?;

    // One atomic GL document for both sides, before commit.
    let mut lines = Vec::new();
    if expense_total > Decimal::ZERO {
        lines.push(line(Gl::InterestExpense, Direction::Debit, expense_total));
        lines.push(line(Gl::AccruedInterestPayable, Direction::Credit, expense_total));
    }
    if income_total > Decimal::ZERO {
        lines.push(line(Gl::AccruedInterestReceivable, Direction::Debit, income_total));
        lines.push(line(Gl::InterestIncome, Direction::Credit, income_total));
    }
    post_balanced(&state, &format!("ACCR-{}", req.as_of), "Daily interest accrual", lines).await?;

    tx.commit().await?;

    Ok(Json(AccrueResponse {
        accrual_date: req.as_of,
        expense_total,
        income_total,
        economic_event_id: event_id,
    }))
}

// ---------------------------------------------------------------------------
// capitalise (monthly)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct CapitaliseRequest {
    /// Accounting month, `YYYY-MM`.
    period: String,
}

#[derive(Debug, Serialize)]
struct CapitaliseResponse {
    period: String,
    deposit_total: Decimal,
    asset_total: Decimal,
    maintenance_total: Decimal,
    economic_event_id: Uuid,
}

/// Parse `YYYY-MM` into the half-open date range `[first_of_month, first_of_next)`.
fn month_range(period: &str) -> Result<(chrono::NaiveDate, chrono::NaiveDate), AppError> {
    let bad = || AppError::BadRequest("period must be YYYY-MM".to_string());
    let (y, m) = period.split_once('-').ok_or_else(bad)?;
    let year: i32 = y.parse().map_err(|_| bad())?;
    let month: u32 = m.parse().map_err(|_| bad())?;
    let start = chrono::NaiveDate::from_ymd_opt(year, month, 1).ok_or_else(bad)?;
    let end = if month == 12 {
        chrono::NaiveDate::from_ymd_opt(year + 1, 1, 1)
    } else {
        chrono::NaiveDate::from_ymd_opt(year, month + 1, 1)
    }
    .ok_or_else(bad)?;
    Ok((start, end))
}

/// Reclass the period's uncapitalised accruals into customer balances and charge
/// the monthly maintenance fee. Idempotent per period.
async fn capitalise(
    State(state): State<AppState>,
    _svc: AuthenticatedService,
    Json(req): Json<CapitaliseRequest>,
) -> Result<Json<CapitaliseResponse>, AppError> {
    let (start, end) = month_range(&req.period)?;

    if let Some(row) = sqlx::query_as::<_, (Uuid, Decimal, Decimal, Decimal)>(
        "SELECT economic_event_id, deposit_total, asset_total, maintenance_total \
         FROM capitalisation_runs WHERE period = $1 AND status = 'completed'",
    )
    .bind(&req.period)
    .fetch_optional(&state.pool)
    .await?
    {
        return Ok(Json(CapitaliseResponse {
            period: req.period,
            economic_event_id: row.0,
            deposit_total: row.1,
            asset_total: row.2,
            maintenance_total: row.3,
        }));
    }

    let event_id = Uuid::new_v4();
    let cfg = state.settings.finance_config();
    let cash_id = external_cash_id(&state).await?;
    let mut tx = state.pool.begin().await?;

    // Deposit side: credit each customer's deposit account by its accrued interest.
    let deposit_rows = sqlx::query_as::<_, (Uuid, Uuid, Decimal)>(
        "SELECT ia.account_id, a.customer_id, SUM(ia.amount) \
         FROM interest_accruals ia JOIN accounts a ON a.account_id = ia.account_id \
         WHERE ia.side = 'expense' AND ia.capitalised = FALSE \
           AND ia.accrual_date >= $1 AND ia.accrual_date < $2 \
         GROUP BY ia.account_id, a.customer_id",
    )
    .bind(start).bind(end)
    .fetch_all(&mut *tx)
    .await?;

    let mut deposit_total = Decimal::ZERO;
    for (account_id, customer_id, amount) in &deposit_rows {
        let reference = reference_number("INT");
        let txn_id: Uuid = sqlx::query_scalar(
            "INSERT INTO transactions \
               (reference_number, transaction_type, amount, description, status, initiated_by, \
                completed_at, product, cost_centre, economic_event_id) \
             VALUES ($1,'interest',$2,'Interest capitalisation','completed',$3, \
                     CURRENT_TIMESTAMP,'deposit','deposits',$4) RETURNING transaction_id",
        )
        .bind(&reference).bind(amount).bind(customer_id).bind(event_id)
        .fetch_one(&mut *tx).await?;
        // Customer credit (balance up); EXTERNAL_CASH debit.
        zero_available(&mut tx, *account_id).await?;
        post_two_legged(&mut tx, txn_id, cash_id, "debit", *account_id, "credit", *amount).await?;
        recompute_available(&mut tx, *account_id).await?;
        deposit_total += *amount;
    }

    // Asset side: raise each card's owed balance by its accrued interest.
    let asset_rows = sqlx::query_as::<_, (Uuid, Uuid, Decimal)>(
        "SELECT ia.account_id, a.customer_id, SUM(ia.amount) \
         FROM interest_accruals ia JOIN accounts a ON a.account_id = ia.account_id \
         WHERE ia.side = 'income' AND ia.capitalised = FALSE \
           AND ia.accrual_date >= $1 AND ia.accrual_date < $2 \
         GROUP BY ia.account_id, a.customer_id",
    )
    .bind(start).bind(end)
    .fetch_all(&mut *tx)
    .await?;

    let mut asset_total = Decimal::ZERO;
    for (account_id, customer_id, amount) in &asset_rows {
        let reference = reference_number("INT");
        let txn_id: Uuid = sqlx::query_scalar(
            "INSERT INTO transactions \
               (reference_number, transaction_type, amount, description, status, initiated_by, \
                completed_at, product, cost_centre, economic_event_id) \
             VALUES ($1,'interest',$2,'Interest capitalisation','completed',$3, \
                     CURRENT_TIMESTAMP,'card','lending',$4) RETURNING transaction_id",
        )
        .bind(&reference).bind(amount).bind(customer_id).bind(event_id)
        .fetch_one(&mut *tx).await?;
        // Card credit (owed up); EXTERNAL_CASH debit.
        zero_available(&mut tx, *account_id).await?;
        post_two_legged(&mut tx, txn_id, cash_id, "debit", *account_id, "credit", *amount).await?;
        recompute_available(&mut tx, *account_id).await?;
        asset_total += *amount;
    }

    // Mark this period's accruals capitalised.
    sqlx::query(
        "UPDATE interest_accruals SET capitalised = TRUE \
         WHERE capitalised = FALSE AND accrual_date >= $1 AND accrual_date < $2",
    )
    .bind(start).bind(end)
    .execute(&mut *tx).await?;

    // Monthly maintenance fee on real customer deposit accounts. `CUSTOMER_ACCOUNT`
    // excludes the bank's own system accounts — so a real customer with a large
    // overdraft is no longer silently exempt (as the old `overdraft_limit` filter
    // would have made them).
    let fee_rows = sqlx::query_as::<_, (Uuid, Uuid, Decimal, Decimal)>(&format!(
        "SELECT a.account_id, a.customer_id, a.balance, a.overdraft_limit FROM accounts a \
         JOIN customers c ON c.customer_id = a.customer_id \
         WHERE a.status = 'active' AND a.account_type IN ('chequing','savings') \
           AND {CUSTOMER_ACCOUNT}"
    ))
    .fetch_all(&mut *tx)
    .await?;

    let mut maintenance_total = Decimal::ZERO;
    for (account_id, customer_id, balance, overdraft_limit) in &fee_rows {
        let fee = maintenance_due(*balance, &cfg);
        if fee.is_zero() {
            continue;
        }
        // Fee floor: never force an account past its overdraft limit.
        if *balance - fee < -*overdraft_limit {
            tracing::warn!(%account_id, "maintenance fee deferred: would breach overdraft limit");
            continue;
        }
        let reference = reference_number("FEE");
        let txn_id: Uuid = sqlx::query_scalar(
            "INSERT INTO transactions \
               (reference_number, transaction_type, amount, description, status, initiated_by, \
                completed_at, product, cost_centre, economic_event_id) \
             VALUES ($1,'fee',$2,'Monthly account maintenance fee','completed',$3, \
                     CURRENT_TIMESTAMP,'deposit','deposits',$4) RETURNING transaction_id",
        )
        .bind(&reference).bind(fee).bind(customer_id).bind(event_id)
        .fetch_one(&mut *tx).await?;
        // Customer debit (balance down); EXTERNAL_CASH credit.
        zero_available(&mut tx, *account_id).await?;
        post_two_legged(&mut tx, txn_id, *account_id, "debit", cash_id, "credit", fee).await?;
        recompute_available(&mut tx, *account_id).await?;
        maintenance_total += fee;
    }

    sqlx::query(
        "INSERT INTO capitalisation_runs \
           (period, economic_event_id, deposit_total, asset_total, maintenance_total) \
         VALUES ($1,$2,$3,$4,$5)",
    )
    .bind(&req.period).bind(event_id).bind(deposit_total).bind(asset_total).bind(maintenance_total)
    .execute(&mut *tx).await?;

    // One atomic GL document for all three reclass/fee movements, before commit.
    let mut lines = Vec::new();
    if deposit_total > Decimal::ZERO {
        lines.push(line(Gl::AccruedInterestPayable, Direction::Debit, deposit_total));
        lines.push(line(Gl::CustomerDeposits, Direction::Credit, deposit_total));
    }
    if asset_total > Decimal::ZERO {
        lines.push(line(Gl::CardReceivable, Direction::Debit, asset_total));
        lines.push(line(Gl::AccruedInterestReceivable, Direction::Credit, asset_total));
    }
    if maintenance_total > Decimal::ZERO {
        lines.push(line(Gl::CustomerDeposits, Direction::Debit, maintenance_total));
        lines.push(line(Gl::FeeIncome, Direction::Credit, maintenance_total));
    }
    post_balanced(&state, &format!("CAP-{}", req.period), "Monthly capitalisation", lines).await?;

    tx.commit().await?;

    Ok(Json(CapitaliseResponse {
        period: req.period,
        deposit_total,
        asset_total,
        maintenance_total,
        economic_event_id: event_id,
    }))
}

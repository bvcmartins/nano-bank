//! Customer-plane money movement: deposit, withdrawal, transfer, history.
//!
//! Each operation is **dual-posted**, mirroring the card rails: the local
//! double-entry subledger (`transaction_entries`, whose triggers maintain
//! `accounts.balance`) **and** the aggregate GL to the accounting core via the
//! Ledger port. A core that's down fails the operation (503) and rolls back the
//! local tx, so the books never drift.
//!
//! Money movement is for **deposit accounts** (chequing/savings). Credit cards
//! move through `/cards/*`, not here.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use rust_decimal::Decimal;
use uuid::Uuid;
use validator::Validate;

use crate::errors::AppError;
use crate::handlers::cards::ensure_system_accounts;
use crate::handlers::AppState;
use crate::ledger::{Account as GlAccount, Direction, EntryLine, NewEntry};
use crate::middleware::auth::AuthenticatedCustomer;
use crate::models::account::{Account, AccountStatus, AccountType};
use crate::models::transaction::{
    DepositRequest, MoneyTransferRequest, Transaction, TransactionEntry, TransactionHistoryQuery,
    TransactionHistoryResponse, TransactionResponse, WithdrawalRequest,
};

pub fn transaction_routes() -> Router<AppState> {
    Router::new()
        .route("/", get(get_transactions))
        .route("/transfer", post(transfer_money))
        .route("/deposit", post(deposit_money))
        .route("/withdrawal", post(withdraw_money))
}

type Tx<'a> = sqlx::Transaction<'a, sqlx::Postgres>;

const ACCOUNT_COLUMNS: &str = "account_id, customer_id, account_number, account_type, currency, \
    balance, available_balance, status, interest_rate, overdraft_limit, minimum_balance, \
    created_at, updated_at, activated_at, closed_at";

const TXN_COLUMNS: &str = "transaction_id, reference_number, transaction_type, amount, currency, \
    description, status, initiated_by, external_reference, metadata, created_at, processed_at, \
    completed_at, failed_at, failure_reason";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// A reference number matching `^[A-Z0-9]{10,20}$`: `prefix` + 12 digits.
fn reference_number(prefix: &str) -> String {
    let n = (Uuid::new_v4().as_u128() % 1_000_000_000_000) as u64;
    format!("{}{:012}", prefix, n)
}

/// Round to 2 dp (the schema rejects anything else) and reject non-positive.
fn normalize_amount(amount: Decimal) -> Result<Decimal, AppError> {
    let amount = amount.round_dp(2);
    if amount <= Decimal::ZERO {
        return Err(AppError::BadRequest("amount must be positive".to_string()));
    }
    Ok(amount)
}

async fn lock_account(tx: &mut Tx<'_>, account_id: Uuid) -> Result<Option<Account>, sqlx::Error> {
    sqlx::query_as::<_, Account>(&format!(
        "SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE account_id = $1 FOR UPDATE"
    ))
    .bind(account_id)
    .fetch_optional(&mut **tx)
    .await
}

/// A deposit account the caller owns and may move money on.
fn ensure_owned_movable(acct: &Account, customer_id: Uuid) -> Result<(), AppError> {
    // 404 (not 403) so other customers' account ids aren't confirmable.
    if acct.customer_id != customer_id {
        return Err(AppError::NotFound("Account not found".to_string()));
    }
    ensure_movable(acct)
}

/// Any account that can receive money movement (active, not a credit card).
fn ensure_movable(acct: &Account) -> Result<(), AppError> {
    if matches!(acct.account_type, AccountType::CreditCard) {
        return Err(AppError::BadRequest(
            "credit card accounts use the card rails (/cards/*), not money movement".to_string(),
        ));
    }
    if !matches!(acct.status, AccountStatus::Active) {
        return Err(AppError::InvalidAccountStatus);
    }
    Ok(())
}

/// Funds available = current balance plus any overdraft headroom.
fn ensure_funds(acct: &Account, amount: Decimal) -> Result<(), AppError> {
    if acct.balance + acct.overdraft_limit < amount {
        return Err(AppError::InsufficientFunds);
    }
    Ok(())
}

/// Insert the completed transaction header; return `(id, reference_number)`.
async fn insert_transaction(
    tx: &mut Tx<'_>,
    txn_type: &str,
    amount: Decimal,
    description: &str,
    initiated_by: Uuid,
) -> Result<(Uuid, String), AppError> {
    sqlx::query_as::<_, (Uuid, String)>(
        "INSERT INTO transactions
            (reference_number, transaction_type, amount, description, status, initiated_by, completed_at)
         VALUES ($1, $2, $3, $4, 'completed', $5, CURRENT_TIMESTAMP)
         RETURNING transaction_id, reference_number",
    )
    .bind(reference_number("TXN"))
    .bind(txn_type)
    .bind(amount)
    .bind(description)
    .bind(initiated_by)
    .fetch_one(&mut **tx)
    .await
    .map_err(AppError::Database)
}

/// Local double-entry: debit `debit_account`, credit `credit_account`, in ONE
/// statement so `trigger_validate_transaction_balance` sees a balanced txn. The
/// balance triggers fill `balance_before/after` and update `accounts.balance`.
async fn post_two_legged(
    tx: &mut Tx<'_>,
    transaction_id: Uuid,
    debit_account: Uuid,
    credit_account: Uuid,
    amount: Decimal,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO transaction_entries
            (transaction_id, account_id, entry_type, amount, balance_before, balance_after, entry_order)
        VALUES
            ($1, $2, 'debit',  $4, 0, 0, 1),
            ($1, $3, 'credit', $4, 0, 0, 2)
        "#,
    )
    .bind(transaction_id)
    .bind(debit_account)
    .bind(credit_account)
    .bind(amount)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

/// Drop `available_balance` to 0 before a balance-lowering (debit) post, so the
/// `available_balance <= balance + overdraft_limit` CHECK holds while the balance
/// trigger runs (it updates `balance` but not `available_balance`). Safe because
/// funds were already checked, so `balance + overdraft_limit >= 0` throughout.
async fn floor_available(tx: &mut Tx<'_>, account_id: Uuid) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE accounts SET available_balance = 0 WHERE account_id = $1")
        .bind(account_id)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

/// Deposit accounts carry no holds, so available = balance + overdraft headroom.
async fn sync_available(tx: &mut Tx<'_>, account_id: Uuid) -> Result<(), sqlx::Error> {
    sqlx::query(
        "UPDATE accounts SET available_balance = balance + overdraft_limit, \
         updated_at = CURRENT_TIMESTAMP WHERE account_id = $1",
    )
    .bind(account_id)
    .execute(&mut **tx)
    .await?;
    Ok(())
}

/// Mirror the GL effect to the accounting core via the Ledger port. A transport
/// failure fails the whole operation (503) so the local books can't drift.
async fn post_gl(
    state: &AppState,
    reference: &str,
    description: &str,
    debit: GlAccount,
    credit: GlAccount,
    amount: Decimal,
) -> Result<(), AppError> {
    state
        .ledger
        .post_entry(NewEntry {
            reference: Some(reference.to_string()),
            description: Some(description.to_string()),
            lines: vec![
                EntryLine {
                    account: debit,
                    direction: Direction::Debit,
                    amount,
                },
                EntryLine {
                    account: credit,
                    direction: Direction::Credit,
                    amount,
                },
            ],
        })
        .await
        .map(|_| ())
        .map_err(|e| AppError::ServiceUnavailable(format!("GL core post failed: {e}")))
}

/// Build the response for a committed transaction, populating only the caller's
/// own legs (so a cross-customer transfer doesn't leak the counterparty).
async fn build_response(
    state: &AppState,
    transaction_id: Uuid,
    customer_id: Uuid,
) -> Result<TransactionResponse, AppError> {
    let txn = sqlx::query_as::<_, Transaction>(&format!(
        "SELECT {TXN_COLUMNS} FROM transactions WHERE transaction_id = $1"
    ))
    .bind(transaction_id)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let entries = sqlx::query_as::<_, TransactionEntry>(
        "SELECT e.entry_id, e.transaction_id, e.account_id, e.entry_type, e.amount, \
                e.balance_before, e.balance_after, e.entry_order, e.created_at \
         FROM transaction_entries e \
         JOIN accounts a ON a.account_id = e.account_id \
         WHERE e.transaction_id = $1 AND a.customer_id = $2 \
         ORDER BY e.entry_order",
    )
    .bind(transaction_id)
    .bind(customer_id)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let mut resp: TransactionResponse = txn.into();
    resp.entries = entries.into_iter().map(Into::into).collect();
    Ok(resp)
}

// ---------------------------------------------------------------------------
// deposit
// ---------------------------------------------------------------------------

async fn deposit_money(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Json(req): Json<DepositRequest>,
) -> Result<(StatusCode, Json<TransactionResponse>), AppError> {
    req.validate()?;
    let amount = normalize_amount(req.amount)?;
    let system = ensure_system_accounts(&state.pool)
        .await
        .map_err(AppError::Database)?;

    let mut tx = state.pool.begin().await?;
    let acct = lock_account(&mut tx, req.account_id)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    ensure_owned_movable(&acct, auth.customer_id)?;
    let _cash = lock_account(&mut tx, system.bank_settlement_id).await?;

    let (txn_id, reference) = insert_transaction(
        &mut tx,
        "deposit",
        amount,
        &req.description,
        auth.customer_id,
    )
    .await?;
    // Customer account is credited (+); the bank's cash account is debited.
    post_two_legged(
        &mut tx,
        txn_id,
        system.bank_settlement_id,
        req.account_id,
        amount,
    )
    .await?;
    sync_available(&mut tx, req.account_id).await?;

    // GL of record: cash (asset) up, customer-deposit liability up.
    post_gl(
        &state,
        &reference,
        "deposit",
        GlAccount::Bank,
        GlAccount::Payable,
        amount,
    )
    .await?;
    tx.commit().await?;

    tracing::info!(account_id = %req.account_id, %amount, "💵 deposit posted");
    let resp = build_response(&state, txn_id, auth.customer_id).await?;
    Ok((StatusCode::CREATED, Json(resp)))
}

// ---------------------------------------------------------------------------
// withdrawal
// ---------------------------------------------------------------------------

async fn withdraw_money(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Json(req): Json<WithdrawalRequest>,
) -> Result<(StatusCode, Json<TransactionResponse>), AppError> {
    req.validate()?;
    let amount = normalize_amount(req.amount)?;
    let system = ensure_system_accounts(&state.pool)
        .await
        .map_err(AppError::Database)?;

    let mut tx = state.pool.begin().await?;
    let acct = lock_account(&mut tx, req.account_id)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    ensure_owned_movable(&acct, auth.customer_id)?;
    ensure_funds(&acct, amount)?;
    let _cash = lock_account(&mut tx, system.bank_settlement_id).await?;

    let (txn_id, reference) = insert_transaction(
        &mut tx,
        "withdrawal",
        amount,
        &req.description,
        auth.customer_id,
    )
    .await?;
    // Customer account is debited (−); the bank's cash account is credited.
    floor_available(&mut tx, req.account_id).await?;
    post_two_legged(
        &mut tx,
        txn_id,
        req.account_id,
        system.bank_settlement_id,
        amount,
    )
    .await?;
    sync_available(&mut tx, req.account_id).await?;

    // GL of record: customer-deposit liability down, cash (asset) down.
    post_gl(
        &state,
        &reference,
        "withdrawal",
        GlAccount::Payable,
        GlAccount::Bank,
        amount,
    )
    .await?;
    tx.commit().await?;

    tracing::info!(account_id = %req.account_id, %amount, "💸 withdrawal posted");
    let resp = build_response(&state, txn_id, auth.customer_id).await?;
    Ok((StatusCode::CREATED, Json(resp)))
}

// ---------------------------------------------------------------------------
// transfer
// ---------------------------------------------------------------------------

async fn transfer_money(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Json(req): Json<MoneyTransferRequest>,
) -> Result<(StatusCode, Json<TransactionResponse>), AppError> {
    req.validate()?;
    let amount = normalize_amount(req.amount)?;
    if req.from_account_id == req.to_account_id {
        return Err(AppError::BadRequest(
            "from and to accounts must differ".to_string(),
        ));
    }

    let mut tx = state.pool.begin().await?;
    // Lock the two accounts in a stable order (by id) to avoid deadlocks between
    // concurrent transfers touching the same pair.
    let (lock1, lock2) = if req.from_account_id <= req.to_account_id {
        (req.from_account_id, req.to_account_id)
    } else {
        (req.to_account_id, req.from_account_id)
    };
    let a1 = lock_account(&mut tx, lock1)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    let a2 = lock_account(&mut tx, lock2)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    let (from_acct, to_acct) = if lock1 == req.from_account_id {
        (a1, a2)
    } else {
        (a2, a1)
    };

    // Caller must own the source; destination can be any movable account.
    ensure_owned_movable(&from_acct, auth.customer_id)?;
    ensure_movable(&to_acct)?;
    ensure_funds(&from_acct, amount)?;

    let (txn_id, reference) = insert_transaction(
        &mut tx,
        "transfer",
        amount,
        &req.description,
        auth.customer_id,
    )
    .await?;
    // Source debited (−), destination credited (+).
    floor_available(&mut tx, req.from_account_id).await?;
    post_two_legged(
        &mut tx,
        txn_id,
        req.from_account_id,
        req.to_account_id,
        amount,
    )
    .await?;
    sync_available(&mut tx, req.from_account_id).await?;
    sync_available(&mut tx, req.to_account_id).await?;

    // GL of record: an internal transfer is a wash on the bank's books (both
    // sides are customer-deposit liabilities) — record a balanced Payable entry.
    post_gl(
        &state,
        &reference,
        "transfer",
        GlAccount::Payable,
        GlAccount::Payable,
        amount,
    )
    .await?;
    tx.commit().await?;

    tracing::info!(from = %req.from_account_id, to = %req.to_account_id, %amount, "🔁 transfer posted");
    let resp = build_response(&state, txn_id, auth.customer_id).await?;
    Ok((StatusCode::CREATED, Json(resp)))
}

// ---------------------------------------------------------------------------
// history
// ---------------------------------------------------------------------------

async fn get_transactions(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Query(params): Query<TransactionHistoryQuery>,
) -> Result<Json<TransactionHistoryResponse>, AppError> {
    params.validate()?;
    let limit = params.limit.unwrap_or(50).clamp(1, 100) as i64;
    let offset = params.offset.unwrap_or(0) as i64;

    // Filters are bound-or-NULL so one query covers every combination. Scoping to
    // the caller's accounts also enforces ownership of any account_id filter.
    let filter = "a.customer_id = $1 \
        AND ($2::uuid IS NULL OR e.account_id = $2) \
        AND ($3::text IS NULL OR t.transaction_type = $3) \
        AND ($4::transaction_status IS NULL OR t.status = $4) \
        AND ($5::timestamptz IS NULL OR t.created_at >= $5) \
        AND ($6::timestamptz IS NULL OR t.created_at <= $6)";

    let total_count: i64 = sqlx::query_scalar(&format!(
        "SELECT count(DISTINCT t.transaction_id) \
         FROM transactions t \
         JOIN transaction_entries e ON e.transaction_id = t.transaction_id \
         JOIN accounts a ON a.account_id = e.account_id \
         WHERE {filter}"
    ))
    .bind(auth.customer_id)
    .bind(params.account_id)
    .bind(params.transaction_type.as_deref())
    .bind(params.status.clone())
    .bind(params.start_date)
    .bind(params.end_date)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let txns = sqlx::query_as::<_, Transaction>(&format!(
        "SELECT DISTINCT {} \
         FROM transactions t \
         JOIN transaction_entries e ON e.transaction_id = t.transaction_id \
         JOIN accounts a ON a.account_id = e.account_id \
         WHERE {filter} \
         ORDER BY t.created_at DESC \
         LIMIT $7 OFFSET $8",
        TXN_COLUMNS
            .split(", ")
            .map(|c| format!("t.{c}"))
            .collect::<Vec<_>>()
            .join(", ")
    ))
    .bind(auth.customer_id)
    .bind(params.account_id)
    .bind(params.transaction_type.as_deref())
    .bind(params.status.clone())
    .bind(params.start_date)
    .bind(params.end_date)
    .bind(limit)
    .bind(offset)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    // Attach each transaction's caller-owned legs.
    let mut transactions = Vec::with_capacity(txns.len());
    for txn in txns {
        let id = txn.transaction_id;
        let entries = sqlx::query_as::<_, TransactionEntry>(
            "SELECT e.entry_id, e.transaction_id, e.account_id, e.entry_type, e.amount, \
                    e.balance_before, e.balance_after, e.entry_order, e.created_at \
             FROM transaction_entries e \
             JOIN accounts a ON a.account_id = e.account_id \
             WHERE e.transaction_id = $1 AND a.customer_id = $2 \
             ORDER BY e.entry_order",
        )
        .bind(id)
        .bind(auth.customer_id)
        .fetch_all(&state.pool)
        .await
        .map_err(AppError::Database)?;

        let mut resp: TransactionResponse = txn.into();
        resp.entries = entries.into_iter().map(Into::into).collect();
        transactions.push(resp);
    }

    let returned = transactions.len() as i64;
    let has_more = offset + returned < total_count;
    Ok(Json(TransactionHistoryResponse {
        transactions,
        total_count: total_count as u64,
        has_more,
        next_offset: has_more.then_some((offset + returned) as u32),
    }))
}

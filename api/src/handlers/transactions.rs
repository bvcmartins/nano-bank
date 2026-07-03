//! Customer-plane money movement: deposit, withdrawal, transfer, history,
//! single-fetch, and reversal — with idempotency, account limits, and fees.
//!
//! Each money-moving operation is **dual-posted**, mirroring the card rails: the
//! local double-entry subledger (`transaction_entries`, whose triggers maintain
//! `accounts.balance`) **and** the aggregate GL to the accounting core via the
//! Ledger port. A core that's down fails the operation (503) and rolls back the
//! local tx, so the books never drift.
//!
//! Money movement is for **deposit accounts** (chequing/savings). Credit cards
//! move through `/cards/*`, not here.

use axum::{
    extract::{Path, Query, State},
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
    DepositRequest, EntryType, MoneyTransferRequest, ReverseRequest, Transaction, TransactionEntry,
    TransactionHistoryQuery, TransactionHistoryResponse, TransactionResponse, TransactionStatus,
    WithdrawalRequest,
};

pub fn transaction_routes() -> Router<AppState> {
    Router::new()
        .route("/", get(get_transactions))
        .route("/transfer", post(transfer_money))
        .route("/deposit", post(deposit_money))
        .route("/withdrawal", post(withdraw_money))
        .route("/:id", get(get_transaction))
        .route("/:id/reverse", post(reverse_transaction))
}

type Tx<'a> = sqlx::Transaction<'a, sqlx::Postgres>;

const ACCOUNT_COLUMNS: &str = "account_id, customer_id, account_number, account_type, currency, \
    balance, available_balance, status, interest_rate, overdraft_limit, minimum_balance, \
    created_at, updated_at, activated_at, closed_at";

const TXN_COLUMNS: &str = "transaction_id, reference_number, transaction_type, amount, currency, \
    description, status, initiated_by, external_reference, metadata, created_at, processed_at, \
    completed_at, failed_at, failure_reason";

/// Flat per-transfer fee. Placeholder schedule — tune or move to config later.
fn transfer_fee() -> Decimal {
    Decimal::new(150, 2) // $1.50
}

enum LimitKind {
    Withdrawal,
    Transfer,
}

// ---------------------------------------------------------------------------
// small helpers
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

/// Insert a completed transaction header; return `(id, reference_number)`.
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
/// trigger runs. `sync_available` restores the correct value afterwards. Safe
/// because funds were checked, so `balance + overdraft_limit >= 0` throughout.
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

/// Build the response for a transaction, populating only the caller's own legs
/// (so a cross-customer transfer doesn't leak the counterparty).
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
// idempotency
// ---------------------------------------------------------------------------

/// The transaction a prior request with this key produced, if any.
async fn find_idempotent(
    state: &AppState,
    customer_id: Uuid,
    key: &str,
) -> Result<Option<Uuid>, AppError> {
    sqlx::query_scalar(
        "SELECT transaction_id FROM idempotency_keys WHERE customer_id = $1 AND idempotency_key = $2",
    )
    .bind(customer_id)
    .bind(key)
    .fetch_optional(&state.pool)
    .await
    .map_err(AppError::Database)
}

/// Record the key against the new transaction, in the same tx. `Ok(false)` means
/// a concurrent request already claimed it (unique violation) — the caller should
/// roll back and return the original.
async fn record_key_in_tx(
    tx: &mut Tx<'_>,
    customer_id: Uuid,
    key: &str,
    transaction_id: Uuid,
) -> Result<bool, AppError> {
    match sqlx::query(
        "INSERT INTO idempotency_keys (customer_id, idempotency_key, transaction_id) \
         VALUES ($1, $2, $3)",
    )
    .bind(customer_id)
    .bind(key)
    .bind(transaction_id)
    .execute(&mut **tx)
    .await
    {
        Ok(_) => Ok(true),
        Err(sqlx::Error::Database(db)) if db.code().as_deref() == Some("23505") => Ok(false),
        Err(e) => Err(AppError::Database(e)),
    }
}

// ---------------------------------------------------------------------------
// limits
// ---------------------------------------------------------------------------

/// Enforce and consume the relevant `account_limits` counter on the debited
/// account, resetting on daily/monthly/annual rollover. Over-limit → 400.
async fn enforce_limit(
    tx: &mut Tx<'_>,
    account_id: Uuid,
    amount: Decimal,
    kind: LimitKind,
) -> Result<(), AppError> {
    // Lazily create a default limits row (schema supplies the default caps).
    sqlx::query(
        "INSERT INTO account_limits (account_id) VALUES ($1) ON CONFLICT (account_id) DO NOTHING",
    )
    .bind(account_id)
    .execute(&mut **tx)
    .await
    .map_err(AppError::Database)?;

    // Reset rolled-over counters, lock the row, and return caps + used.
    let (dwl, dwu, dtl, dtu, mtl, mtu, atl, atu): (
        Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal,
    ) = sqlx::query_as(
        "UPDATE account_limits SET
            daily_withdrawal_used = CASE WHEN last_reset_date < CURRENT_DATE THEN 0 ELSE daily_withdrawal_used END,
            daily_transfer_used   = CASE WHEN last_reset_date < CURRENT_DATE THEN 0 ELSE daily_transfer_used END,
            monthly_transfer_used = CASE WHEN date_trunc('month', last_reset_date::timestamp) < date_trunc('month', CURRENT_DATE::timestamp) THEN 0 ELSE monthly_transfer_used END,
            annual_transfer_used  = CASE WHEN date_trunc('year',  last_reset_date::timestamp) < date_trunc('year',  CURRENT_DATE::timestamp) THEN 0 ELSE annual_transfer_used END,
            last_reset_date = CURRENT_DATE,
            updated_at = CURRENT_TIMESTAMP
         WHERE account_id = $1
         RETURNING daily_withdrawal_limit, daily_withdrawal_used, daily_transfer_limit, daily_transfer_used,
                   monthly_transfer_limit, monthly_transfer_used, annual_transfer_limit, annual_transfer_used",
    )
    .bind(account_id)
    .fetch_one(&mut **tx)
    .await
    .map_err(AppError::Database)?;

    match kind {
        LimitKind::Withdrawal => {
            if dwu + amount > dwl {
                return Err(AppError::TransactionLimitExceeded);
            }
            sqlx::query(
                "UPDATE account_limits SET daily_withdrawal_used = daily_withdrawal_used + $2 WHERE account_id = $1",
            )
            .bind(account_id)
            .bind(amount)
            .execute(&mut **tx)
            .await
            .map_err(AppError::Database)?;
        }
        LimitKind::Transfer => {
            if dtu + amount > dtl || mtu + amount > mtl || atu + amount > atl {
                return Err(AppError::TransactionLimitExceeded);
            }
            sqlx::query(
                "UPDATE account_limits SET \
                    daily_transfer_used   = daily_transfer_used + $2, \
                    monthly_transfer_used = monthly_transfer_used + $2, \
                    annual_transfer_used  = annual_transfer_used + $2 \
                 WHERE account_id = $1",
            )
            .bind(account_id)
            .bind(amount)
            .execute(&mut **tx)
            .await
            .map_err(AppError::Database)?;
        }
    }
    Ok(())
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

    if let Some(key) = req.idempotency_key.as_deref() {
        if let Some(orig) = find_idempotent(&state, auth.customer_id, key).await? {
            return Ok((
                StatusCode::OK,
                Json(build_response(&state, orig, auth.customer_id).await?),
            ));
        }
    }

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

    if let Some(resp) = commit_or_replay(
        &state,
        &mut tx,
        &req.idempotency_key,
        txn_id,
        auth.customer_id,
    )
    .await?
    {
        return Ok((StatusCode::OK, Json(resp)));
    }

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

/// Record the idempotency key (if any) inside the tx. Returns `Some(response)` if
/// a concurrent request already claimed the key — the caller returns it as a 200
/// replay after this tx is rolled back.
async fn commit_or_replay(
    state: &AppState,
    tx: &mut Tx<'_>,
    key: &Option<String>,
    txn_id: Uuid,
    customer_id: Uuid,
) -> Result<Option<TransactionResponse>, AppError> {
    let Some(key) = key.as_deref() else {
        return Ok(None);
    };
    if record_key_in_tx(tx, customer_id, key, txn_id).await? {
        return Ok(None);
    }
    // Lost the race: the key exists on another transaction. Roll back ours and
    // return the original.
    let orig = find_idempotent(state, customer_id, key)
        .await?
        .ok_or_else(|| AppError::Internal("idempotency race with no record".to_string()))?;
    Ok(Some(build_response(state, orig, customer_id).await?))
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

    if let Some(key) = req.idempotency_key.as_deref() {
        if let Some(orig) = find_idempotent(&state, auth.customer_id, key).await? {
            return Ok((
                StatusCode::OK,
                Json(build_response(&state, orig, auth.customer_id).await?),
            ));
        }
    }

    let system = ensure_system_accounts(&state.pool)
        .await
        .map_err(AppError::Database)?;

    let mut tx = state.pool.begin().await?;
    let acct = lock_account(&mut tx, req.account_id)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    ensure_owned_movable(&acct, auth.customer_id)?;
    ensure_funds(&acct, amount)?;
    enforce_limit(&mut tx, req.account_id, amount, LimitKind::Withdrawal).await?;
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

    if let Some(resp) = commit_or_replay(
        &state,
        &mut tx,
        &req.idempotency_key,
        txn_id,
        auth.customer_id,
    )
    .await?
    {
        return Ok((StatusCode::OK, Json(resp)));
    }

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

    if let Some(key) = req.idempotency_key.as_deref() {
        if let Some(orig) = find_idempotent(&state, auth.customer_id, key).await? {
            return Ok((
                StatusCode::OK,
                Json(build_response(&state, orig, auth.customer_id).await?),
            ));
        }
    }

    let fee = transfer_fee();
    let system = ensure_system_accounts(&state.pool)
        .await
        .map_err(AppError::Database)?;

    let mut tx = state.pool.begin().await?;
    // Lock accounts in a stable order (by id) to avoid deadlocks.
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
        (&a1, &a2)
    } else {
        (&a2, &a1)
    };

    ensure_owned_movable(from_acct, auth.customer_id)?;
    ensure_movable(to_acct)?;
    ensure_funds(from_acct, amount + fee)?; // must cover the fee too
    enforce_limit(&mut tx, req.from_account_id, amount, LimitKind::Transfer).await?;
    let _cash = lock_account(&mut tx, system.bank_settlement_id).await?;

    let (txn_id, reference) = insert_transaction(
        &mut tx,
        "transfer",
        amount,
        &req.description,
        auth.customer_id,
    )
    .await?;
    floor_available(&mut tx, req.from_account_id).await?;
    post_two_legged(
        &mut tx,
        txn_id,
        req.from_account_id,
        req.to_account_id,
        amount,
    )
    .await?;

    // Fee is a separate `fee` transaction (keeps this txn's entries to two legs
    // and makes reversal clean). Debit the customer, credit the bank's cash.
    let fee_ref = if fee > Decimal::ZERO {
        let (fee_txn, fee_ref) =
            insert_transaction(&mut tx, "fee", fee, "transfer fee", auth.customer_id).await?;
        post_two_legged(
            &mut tx,
            fee_txn,
            req.from_account_id,
            system.bank_settlement_id,
            fee,
        )
        .await?;
        sqlx::query(
            "INSERT INTO transaction_fees (transaction_id, fee_type, fee_amount) VALUES ($1, 'transfer', $2)",
        )
        .bind(txn_id)
        .bind(fee)
        .execute(&mut *tx)
        .await
        .map_err(AppError::Database)?;
        Some(fee_ref)
    } else {
        None
    };

    sync_available(&mut tx, req.from_account_id).await?;
    sync_available(&mut tx, req.to_account_id).await?;

    if let Some(resp) = commit_or_replay(
        &state,
        &mut tx,
        &req.idempotency_key,
        txn_id,
        auth.customer_id,
    )
    .await?
    {
        return Ok((StatusCode::OK, Json(resp)));
    }

    // GL of record: internal transfer is a wash (Payable↔Payable). The fee is
    // recognised as revenue (customer-deposit liability down, revenue up).
    post_gl(
        &state,
        &reference,
        "transfer",
        GlAccount::Payable,
        GlAccount::Payable,
        amount,
    )
    .await?;
    if let Some(fee_ref) = fee_ref {
        post_gl(
            &state,
            &fee_ref,
            "transfer fee",
            GlAccount::Payable,
            GlAccount::Revenue,
            fee,
        )
        .await?;
    }
    tx.commit().await?;

    tracing::info!(from = %req.from_account_id, to = %req.to_account_id, %amount, %fee, "🔁 transfer posted");
    let resp = build_response(&state, txn_id, auth.customer_id).await?;
    Ok((StatusCode::CREATED, Json(resp)))
}

// ---------------------------------------------------------------------------
// single fetch
// ---------------------------------------------------------------------------

async fn get_transaction(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Path(id): Path<Uuid>,
) -> Result<Json<TransactionResponse>, AppError> {
    let involved: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM transaction_entries e \
         JOIN accounts a ON a.account_id = e.account_id \
         WHERE e.transaction_id = $1 AND a.customer_id = $2)",
    )
    .bind(id)
    .bind(auth.customer_id)
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;

    if !involved {
        return Err(AppError::NotFound("Transaction not found".to_string()));
    }
    Ok(Json(build_response(&state, id, auth.customer_id).await?))
}

// ---------------------------------------------------------------------------
// reversal
// ---------------------------------------------------------------------------

async fn reverse_transaction(
    State(state): State<AppState>,
    auth: AuthenticatedCustomer,
    Path(id): Path<Uuid>,
    Json(req): Json<ReverseRequest>,
) -> Result<(StatusCode, Json<TransactionResponse>), AppError> {
    req.validate()?;

    // Load the original. Only its initiator may reverse it (v1; real banks gate
    // reversals to ops/admin).
    let original = sqlx::query_as::<_, (String, TransactionStatus, Option<Uuid>, Decimal)>(
        "SELECT transaction_type, status, initiated_by, amount FROM transactions WHERE transaction_id = $1",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await
    .map_err(AppError::Database)?
    .ok_or_else(|| AppError::NotFound("Transaction not found".to_string()))?;
    let (orig_type, orig_status, initiated_by, amount) = original;

    if initiated_by != Some(auth.customer_id) {
        return Err(AppError::NotFound("Transaction not found".to_string()));
    }
    if !matches!(orig_status, TransactionStatus::Completed) {
        return Err(AppError::BadRequest(
            "only a completed transaction can be reversed".to_string(),
        ));
    }
    if !matches!(orig_type.as_str(), "deposit" | "withdrawal" | "transfer") {
        return Err(AppError::BadRequest(
            "this transaction type cannot be reversed".to_string(),
        ));
    }

    // The original's two legs identify the accounts. Reversal swaps them.
    let legs = sqlx::query_as::<_, (Uuid, EntryType)>(
        "SELECT account_id, entry_type FROM transaction_entries WHERE transaction_id = $1 ORDER BY entry_order",
    )
    .bind(id)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;
    let orig_debit = legs
        .iter()
        .find(|(_, t)| matches!(t, EntryType::Debit))
        .map(|(a, _)| *a);
    let orig_credit = legs
        .iter()
        .find(|(_, t)| matches!(t, EntryType::Credit))
        .map(|(a, _)| *a);
    let (Some(orig_debit), Some(orig_credit)) = (orig_debit, orig_credit) else {
        return Err(AppError::Internal(
            "original transaction is not two-legged".to_string(),
        ));
    };
    // Reversal debits the account originally credited, credits the one debited.
    let (rev_debit, rev_credit) = (orig_credit, orig_debit);

    let mut tx = state.pool.begin().await?;
    // Lock both involved accounts in a stable order.
    let (lock1, lock2) = if rev_debit <= rev_credit {
        (rev_debit, rev_credit)
    } else {
        (rev_credit, rev_debit)
    };
    let l1 = lock_account(&mut tx, lock1)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    let l2 = lock_account(&mut tx, lock2)
        .await?
        .ok_or_else(|| AppError::NotFound("Account not found".to_string()))?;
    let debit_acct = if lock1 == rev_debit { &l1 } else { &l2 };

    // v1: reject if the reversal would overdraw the debited account (no negative
    // clawback yet).
    ensure_funds(debit_acct, amount)?;

    let (rev_txn, rev_ref) = insert_transaction(
        &mut tx,
        "reversal",
        amount,
        &format!("reversal: {}", req.reason),
        auth.customer_id,
    )
    .await?;
    floor_available(&mut tx, rev_debit).await?;
    post_two_legged(&mut tx, rev_txn, rev_debit, rev_credit, amount).await?;
    sync_available(&mut tx, rev_debit).await?;
    sync_available(&mut tx, rev_credit).await?;

    // Mark the original reversed and cross-link it.
    sqlx::query("UPDATE transactions SET status = 'reversed' WHERE transaction_id = $1")
        .bind(id)
        .execute(&mut *tx)
        .await
        .map_err(AppError::Database)?;
    sqlx::query(
        "INSERT INTO transaction_reversals (original_transaction_id, reversal_transaction_id, reason, authorized_by) \
         VALUES ($1, $2, $3, $4)",
    )
    .bind(id)
    .bind(rev_txn)
    .bind(&req.reason)
    .bind(auth.customer_id)
    .execute(&mut *tx)
    .await
    .map_err(AppError::Database)?;

    // Reverse the GL in the core (opposite of the original by type). The fee, if
    // any, is a separate non-refunded transaction (v1) and is not reversed.
    let (gl_debit, gl_credit) = match orig_type.as_str() {
        "deposit" => (GlAccount::Payable, GlAccount::Bank),
        "withdrawal" => (GlAccount::Bank, GlAccount::Payable),
        _ /* transfer */ => (GlAccount::Payable, GlAccount::Payable),
    };
    post_gl(&state, &rev_ref, "reversal", gl_debit, gl_credit, amount).await?;
    tx.commit().await?;

    tracing::info!(original = %id, reversal = %rev_txn, %amount, "↩️ transaction reversed");
    let resp = build_response(&state, rev_txn, auth.customer_id).await?;
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

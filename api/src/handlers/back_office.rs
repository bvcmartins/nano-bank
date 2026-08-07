//! The back-office read plane.
//!
//! Every other read surface in this API answers the question "what may *this*
//! customer see?" — identity comes from the token and the SQL is scoped to
//! `auth.customer_id`. That is right for the consumer app and for the agent
//! plane, and it is why neither can serve a back-office system: a CRM, a support
//! console or an operations dashboard needs to look at a customer it is not.
//!
//! Before this module, a service token reached only the payment rails
//! (`/cards`, `/interac`, `/aft`, `/lynx`, `/finance`, `/fraud/admin`). There
//! was no `GET /customers/:id`, no customer list, and no way to read KYC
//! documents at all. A back-office integration had exactly two options, both
//! bad: hold every customer's password and log in as them, or bypass the API and
//! read PostgreSQL directly.
//!
//! ## What this plane is, precisely
//!
//! **Read-only, and it can read any customer.** That is a genuine escalation
//! over the rails endpoints, which touch only the caller's own settlement flow,
//! and it deserves to be stated rather than discovered:
//!
//! - The service secret is now sufficient to enumerate customers and read their
//!   balances and transaction history. It was already sufficient to move money
//!   on the rails, so this does not widen *who* is trusted — but it does widen
//!   what a leaked secret exposes, from settlement to personal data.
//! - There are **no write endpoints here**, deliberately. Back-office systems
//!   are the classic confused deputy; a plane that can only read cannot be
//!   talked into moving money.
//! - Responses reuse the existing consumer-plane response types, which already
//!   drop `sin` and `date_of_birth`. A back-office caller has no more need of a
//!   social insurance number than the consumer app does.
//!
//! ## Two surfaces under one plane
//!
//! - **Customer/account reads** (`/customers`, `/accounts/*`) — the CRM/support
//!   surface: per-customer identity, balances, history, KYC.
//! - **Operations aggregates** (`/ops/*`) — the COO's perception surface:
//!   bank-wide float/transactions/rails/exceptions/cards rollups with **no
//!   customer identity** at all. Same service-token boundary, read-only, and it
//!   too reads no fraud table.
//!
//! ## What is deliberately absent
//!
//! **Durable audit of reads.** Every handler emits a structured `tracing` event
//! naming the subject, which is greppable and free, but nothing is written to
//! `audit_logs` — the `audit_action` enum has no `read` variant, and adding one
//! is a schema migration that belongs in its own change rather than riding along
//! with a new endpoint. Recorded here so it is a known gap and not an oversight.
//!
//! **Fraud data.** Nothing here reads `suspicious_activities`,
//! `monitoring_rules` or `rule_violations`. Those tables are unused by design:
//! `nano-bank-fraud-engine` owns that data in its own database and withholds
//! scores and reasons so its case surface cannot become a score oracle. Serving
//! them from a back-office plane would rebuild exactly that oracle, since a
//! caller could bisect the model by observing which customers carry cases.

use axum::{
    extract::{Path, Query, State},
    response::Json,
    routing::get,
    Router,
};
use chrono::{DateTime, Duration, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::config::database::DatabasePool;
use crate::errors::AppError;
use crate::handlers::transactions::fetch_history;
use crate::handlers::AppState;
use crate::middleware::auth::AuthenticatedService;
use crate::models::account::{
    Account, AccountBalanceResponse, AccountResponse, AccountSummary, ActiveHold,
};
use crate::models::customer::{Customer, CustomerResponse, DocumentType, VerificationStatus};
use crate::models::transaction::{TransactionHistoryQuery, TransactionHistoryResponse};

/// Columns shared by every customer read. Mirrors `handlers::customers`.
const CUSTOMER_COLUMNS: &str = "customer_id, email, phone_number, first_name, last_name, \
    date_of_birth, sin, kyc_status, kyc_completed_at, created_at, updated_at";

const ACCOUNT_COLUMNS: &str = "account_id, customer_id, account_number, account_type, currency, \
    balance, available_balance, status, interest_rate, overdraft_limit, minimum_balance, \
    created_at, updated_at, activated_at, closed_at";

/// The bank's synthetic system customers that own the clearing/settlement
/// float. Keyed by **exact email**, never a `@nano.bank` suffix match:
/// `POST /customers` accepts any address that passes email validation, so a real
/// customer could register `anything@nano.bank` and a `LIKE '%@nano.bank'` filter
/// would fold their balance into the bank's float. These are the same fixed
/// identities the rail/cards/finance handlers key off by constant.
const SYSTEM_CUSTOMER_EMAILS: [&str; 5] = [
    "system@nano.bank",
    "interac@nano.bank",
    "aft@nano.bank",
    "lynx@nano.bank",
    "cash@nano.bank",
];

pub fn back_office_routes() -> Router<AppState> {
    Router::new()
        // CRM / support: cross-customer identity reads.
        .route("/customers", get(list_customers))
        .route("/customers/:customer_id", get(get_customer))
        .route("/customers/:customer_id/accounts", get(list_accounts))
        .route(
            "/customers/:customer_id/kyc-documents",
            get(list_kyc_documents),
        )
        .route("/accounts/:account_id", get(get_account))
        .route("/accounts/:account_id/balance", get(get_balance))
        .route("/accounts/:account_id/transactions", get(get_transactions))
        // Operations aggregates: bank-wide rollups, no customer identity.
        .route("/ops/float", get(ops_float))
        .route("/ops/transactions", get(ops_transactions))
        .route("/ops/rails", get(ops_rails))
        .route("/ops/exceptions", get(ops_exceptions))
        .route("/ops/cards", get(ops_cards))
        .route("/ops/declines", get(ops_declines))
}

// ---------------------------------------------------------------------------
// Customers
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct CustomerSearchQuery {
    /// Exact, case-insensitive match. Email is the identifier back-office
    /// systems actually hold, so this is the join key in practice.
    pub email: Option<String>,
    pub limit: Option<u32>,
    pub offset: Option<u32>,
}

/// List or search customers.
///
/// Paginated with the same `limit`/`offset` shape and the same 1..=100 clamp as
/// transaction history, so a caller learns one pagination convention rather than
/// two. Without the clamp this endpoint is a whole-table dump.
async fn list_customers(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Query(q): Query<CustomerSearchQuery>,
) -> Result<Json<Vec<CustomerResponse>>, AppError> {
    let limit = q.limit.unwrap_or(50).clamp(1, 100);
    let offset = q.offset.unwrap_or(0);

    // Two prepared statements rather than a builder: there are exactly two
    // shapes, and `lower(email) = lower($1)` keeps the search sargable against
    // idx_customers_email only if the index matches — it does not, so this is a
    // scan on large tables. Acceptable for a back-office lookup by exact
    // address; a `citext` column or a functional index is the fix if it matters.
    let customers = match q.email.as_deref() {
        Some(email) => {
            sqlx::query_as::<_, Customer>(&format!(
                "SELECT {CUSTOMER_COLUMNS} FROM customers WHERE lower(email) = lower($1) \
                 ORDER BY created_at DESC LIMIT $2 OFFSET $3"
            ))
            .bind(email)
            .bind(limit as i64)
            .bind(offset as i64)
            .fetch_all(&state.pool)
            .await
        }
        None => {
            sqlx::query_as::<_, Customer>(&format!(
                "SELECT {CUSTOMER_COLUMNS} FROM customers \
                 ORDER BY created_at DESC LIMIT $1 OFFSET $2"
            ))
            .bind(limit as i64)
            .bind(offset as i64)
            .fetch_all(&state.pool)
            .await
        }
    }
    .map_err(AppError::Database)?;

    tracing::info!(
        plane = "back_office",
        matched = customers.len(),
        by_email = q.email.is_some(),
        "back-office customer search"
    );

    Ok(Json(customers.into_iter().map(Into::into).collect()))
}

async fn get_customer(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(customer_id): Path<Uuid>,
) -> Result<Json<CustomerResponse>, AppError> {
    let customer = sqlx::query_as::<_, Customer>(&format!(
        "SELECT {CUSTOMER_COLUMNS} FROM customers WHERE customer_id = $1"
    ))
    .bind(customer_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| match e {
        sqlx::Error::RowNotFound => AppError::NotFound("Customer not found".to_string()),
        e => AppError::Database(e),
    })?;

    tracing::info!(
        plane = "back_office",
        customer_id = %customer_id,
        "back-office customer read"
    );

    Ok(Json(customer.into()))
}

// ---------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------

/// A customer's accounts.
///
/// Returns the same `AccountSummary` shape as the consumer plane — no
/// `available_balance`, no `created_at`. Keeping the summary narrow means a
/// back-office list view cannot accidentally become the authoritative source for
/// a figure it only half-fetched; callers that need the detail ask for the
/// account.
async fn list_accounts(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(customer_id): Path<Uuid>,
) -> Result<Json<Vec<AccountSummary>>, AppError> {
    let accounts = sqlx::query_as::<_, Account>(&format!(
        "SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE customer_id = $1 ORDER BY created_at DESC"
    ))
    .bind(customer_id)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    tracing::info!(
        plane = "back_office",
        customer_id = %customer_id,
        accounts = accounts.len(),
        "back-office account list"
    );

    Ok(Json(
        accounts
            .into_iter()
            .map(|a| AccountSummary {
                account_id: a.account_id,
                account_number: a.account_number,
                account_type: a.account_type,
                balance: a.balance,
                currency: a.currency,
                status: a.status,
            })
            .collect(),
    ))
}

/// One account, by id.
///
/// Note the difference from the consumer plane: there is no ownership check,
/// because there is no owner to check against. That is the whole point of the
/// plane and the reason it is read-only.
async fn get_account(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(account_id): Path<Uuid>,
) -> Result<Json<AccountResponse>, AppError> {
    let account = load_account(&state, account_id).await?;

    tracing::info!(
        plane = "back_office",
        account_id = %account_id,
        "back-office account read"
    );

    Ok(Json(account.into()))
}

async fn get_balance(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(account_id): Path<Uuid>,
) -> Result<Json<AccountBalanceResponse>, AppError> {
    let account = load_account(&state, account_id).await?;

    let holds = sqlx::query_as::<_, ActiveHold>(
        "SELECT hold_id, amount, reason, expires_at
         FROM account_holds
         WHERE account_id = $1 AND released_at IS NULL
         ORDER BY created_at DESC",
    )
    .bind(account_id)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    tracing::info!(
        plane = "back_office",
        account_id = %account_id,
        "back-office balance read"
    );

    Ok(Json(AccountBalanceResponse {
        account_id: account.account_id,
        account_number: account.account_number,
        balance: account.balance,
        available_balance: account.available_balance,
        currency: account.currency,
        status: account.status,
        holds,
    }))
}

async fn load_account(state: &AppState, account_id: Uuid) -> Result<Account, AppError> {
    sqlx::query_as::<_, Account>(&format!(
        "SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE account_id = $1"
    ))
    .bind(account_id)
    .fetch_one(&state.pool)
    .await
    .map_err(|e| match e {
        sqlx::Error::RowNotFound => AppError::NotFound("Account not found".to_string()),
        e => AppError::Database(e),
    })
}

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

/// Transaction history for one account.
///
/// Reuses `fetch_history`, which is always scoped to a `customer_id`'s own
/// accounts. Rather than loosening that function — it is the same scoping the
/// consumer and agent planes depend on, and widening it would weaken all three —
/// this resolves the account's owner first and then pins `account_id` to the one
/// requested. Same guarantee, arrived at from the other direction.
async fn get_transactions(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(account_id): Path<Uuid>,
    Query(mut q): Query<TransactionHistoryQuery>,
) -> Result<Json<TransactionHistoryResponse>, AppError> {
    let account = load_account(&state, account_id).await?;

    // A caller cannot widen the scope by also passing ?account_id= — the path
    // wins, so the query is always for exactly the account named in the URL.
    q.account_id = Some(account_id);

    let history = fetch_history(&state, account.customer_id, q).await?;

    tracing::info!(
        plane = "back_office",
        account_id = %account_id,
        returned = history.transactions.len(),
        "back-office transaction history"
    );

    Ok(Json(history))
}

// ---------------------------------------------------------------------------
// KYC documents
// ---------------------------------------------------------------------------

/// A KYC document, minus the bytes.
///
/// `file_path` is deliberately **not** exposed. It is a pointer into the
/// document store, and a back-office system needs to know a passport was
/// verified — not where the scan of it lives. `notes` and `verified_by` are
/// included because they are what an onboarding case is actually about.
#[derive(Debug, Serialize)]
pub struct KycDocumentResponse {
    pub document_id: Uuid,
    pub document_type: DocumentType,
    pub file_name: String,
    pub verification_status: VerificationStatus,
    pub verified_by: Option<String>,
    pub notes: Option<String>,
    pub uploaded_at: chrono::DateTime<chrono::Utc>,
    pub verified_at: Option<chrono::DateTime<chrono::Utc>>,
    pub expires_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, sqlx::FromRow)]
struct KycDocumentRow {
    document_id: Uuid,
    document_type: DocumentType,
    file_name: String,
    verification_status: VerificationStatus,
    verified_by: Option<String>,
    notes: Option<String>,
    created_at: chrono::DateTime<chrono::Utc>,
    verified_at: Option<chrono::DateTime<chrono::Utc>>,
    expires_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// The KYC documents on file for a customer.
///
/// This is the first read path to `kyc_documents` in the API. The table has
/// existed since the initial schema and was reachable only by the upload stub,
/// which returns a plain-text TODO and stores nothing.
async fn list_kyc_documents(
    State(state): State<AppState>,
    _auth: AuthenticatedService,
    Path(customer_id): Path<Uuid>,
) -> Result<Json<Vec<KycDocumentResponse>>, AppError> {
    let rows = sqlx::query_as::<_, KycDocumentRow>(
        "SELECT document_id, document_type, file_name, verification_status,
                verified_by, notes, created_at, verified_at, expires_at
         FROM kyc_documents WHERE customer_id = $1 ORDER BY created_at DESC",
    )
    .bind(customer_id)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    tracing::info!(
        plane = "back_office",
        customer_id = %customer_id,
        documents = rows.len(),
        "back-office kyc document read"
    );

    Ok(Json(
        rows.into_iter()
            .map(|r| KycDocumentResponse {
                document_id: r.document_id,
                document_type: r.document_type,
                file_name: r.file_name,
                verification_status: r.verification_status,
                verified_by: r.verified_by,
                notes: r.notes,
                uploaded_at: r.created_at,
                verified_at: r.verified_at,
                expires_at: r.expires_at,
            })
            .collect(),
    ))
}

// ---------------------------------------------------------------------------
// Operations aggregates (COO perception surface)
// ---------------------------------------------------------------------------
// Service-plane, read-only, bank-wide aggregates with no customer identity;
// every route requires a service token and no fraud table is ever read here.

#[derive(Serialize)]
struct FloatAccount {
    system: String,       // interac | aft | lynx | system | cash
    role: String,         // clearing | settlement | external_cash | other
    account_type: String, // chequing | savings | ...
    balance: Decimal,
}

#[derive(Serialize)]
struct FloatResponse {
    accounts: Vec<FloatAccount>,
    total_float: Decimal,
    /// What `total_float` is and is not. It is a **gross sum** of the system
    /// accounts' balances. Its components are signed per GL convention (clearing
    /// carries the issuer's obligation as a negative; `external_cash` represents
    /// cash *outside* the bank) and are **not economically additive** — read it
    /// as a magnitude, not a net position. Surfaced in the payload so the figure
    /// never travels to the agent without its basis.
    basis: String,
}

const FLOAT_BASIS: &str = "gross sum of system-account balances; components are \
    signed per GL convention (clearing negative, external_cash exogenous) and are \
    not economically additive — a magnitude, not a net position";

#[derive(sqlx::FromRow)]
struct FloatRow {
    email: String,
    account_type: String,
    balance: Decimal,
}

/// The clearing/settlement float: balances of the synthetic system customers'
/// accounts (`*@nano.bank`). `chequing`->clearing, `savings`->settlement, except
/// `cash@nano.bank`'s chequing which is EXTERNAL_CASH.
async fn ops_float(
    _: AuthenticatedService,
    State(state): State<AppState>,
) -> Result<Json<FloatResponse>, AppError> {
    let system_emails: Vec<String> = SYSTEM_CUSTOMER_EMAILS
        .iter()
        .map(|s| s.to_string())
        .collect();
    let rows = sqlx::query_as::<_, FloatRow>(
        "SELECT c.email AS email, a.account_type::text AS account_type, a.balance AS balance
         FROM accounts a
         JOIN customers c ON c.customer_id = a.customer_id
         WHERE c.email = ANY($1)
         ORDER BY c.email, a.account_type",
    )
    .bind(&system_emails)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    let mut accounts = Vec::with_capacity(rows.len());
    let mut total = Decimal::ZERO;
    for r in rows {
        let system = r.email.split('@').next().unwrap_or("").to_string();
        let role = match (system.as_str(), r.account_type.as_str()) {
            ("cash", _) => "external_cash",
            (_, "chequing") => "clearing",
            (_, "savings") => "settlement",
            _ => "other",
        }
        .to_string();
        total += r.balance;
        accounts.push(FloatAccount {
            system,
            role,
            account_type: r.account_type,
            balance: r.balance,
        });
    }
    Ok(Json(FloatResponse {
        accounts,
        total_float: total,
        basis: FLOAT_BASIS.to_string(),
    }))
}

#[derive(Deserialize)]
struct WindowQuery {
    window: Option<String>,
}

/// Map a window shorthand to a cutoff instant. Unknown windows are a 400 so the
/// caller learns the vocabulary rather than getting silent 24h data.
fn window_cutoff(window: &str) -> Result<DateTime<Utc>, AppError> {
    let dur = match window {
        "24h" => Duration::hours(24),
        "7d" => Duration::days(7),
        "30d" => Duration::days(30),
        other => {
            return Err(AppError::BadRequest(format!(
                "unsupported window '{other}' (use 24h|7d|30d)"
            )))
        }
    };
    Ok(Utc::now() - dur)
}

#[derive(Serialize, sqlx::FromRow)]
struct TxnGroup {
    transaction_type: String,
    status: String,
    count: i64,
    total: Decimal,
}

#[derive(Serialize)]
struct TransactionsResponse {
    window: String,
    since: DateTime<Utc>,
    groups: Vec<TxnGroup>,
}

/// Bank-wide transaction counts + amounts grouped by type and status over a
/// window. Read-only aggregate; no customer scoping.
async fn ops_transactions(
    _: AuthenticatedService,
    State(state): State<AppState>,
    Query(q): Query<WindowQuery>,
) -> Result<Json<TransactionsResponse>, AppError> {
    let window = q.window.unwrap_or_else(|| "24h".to_string());
    let since = window_cutoff(&window)?;
    let groups = sqlx::query_as::<_, TxnGroup>(
        "SELECT transaction_type,
                status::text AS status,
                COUNT(*) AS count,
                COALESCE(SUM(amount), 0) AS total
         FROM transactions
         WHERE created_at >= $1
         GROUP BY transaction_type, status
         ORDER BY transaction_type, status",
    )
    .bind(since)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    Ok(Json(TransactionsResponse {
        window,
        since,
        groups,
    }))
}

#[derive(Serialize, sqlx::FromRow)]
struct RailGroup {
    status: String,
    count: i64,
    total: Decimal,
}

#[derive(Serialize)]
struct RailsBreakdown {
    interac: Vec<RailGroup>,
    aft: Vec<RailGroup>,
    lynx: Vec<RailGroup>,
}

#[derive(Serialize)]
struct RailsResponse {
    window: String,
    since: DateTime<Utc>,
    rails: RailsBreakdown,
}

/// Count + summed amount grouped by status for one rail table over a window.
/// `table` is always a hardcoded literal below (never user input), so the
/// interpolation is safe; the window value is bound.
async fn rail_groups(
    pool: &DatabasePool,
    table: &str,
    since: DateTime<Utc>,
) -> Result<Vec<RailGroup>, AppError> {
    let sql = format!(
        "SELECT status::text AS status, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
         FROM {table}
         WHERE created_at >= $1
         GROUP BY status
         ORDER BY status"
    );
    sqlx::query_as::<_, RailGroup>(&sql)
        .bind(since)
        .fetch_all(pool)
        .await
        .map_err(AppError::Database)
}

/// Per-rail activity (Interac / AFT / Lynx) grouped by status over a window —
/// the throughput/backlog signal the COO reads. Read-only aggregate.
async fn ops_rails(
    _: AuthenticatedService,
    State(state): State<AppState>,
    Query(q): Query<WindowQuery>,
) -> Result<Json<RailsResponse>, AppError> {
    let window = q.window.unwrap_or_else(|| "24h".to_string());
    let since = window_cutoff(&window)?;
    let interac = rail_groups(&state.pool, "interac_etransfers", since).await?;
    let aft = rail_groups(&state.pool, "aft_entries", since).await?;
    let lynx = rail_groups(&state.pool, "lynx_wires", since).await?;
    Ok(Json(RailsResponse {
        window,
        since,
        rails: RailsBreakdown { interac, aft, lynx },
    }))
}

#[derive(Serialize)]
struct ExceptionCounts {
    failed_transactions: i64,
    reversals: i64,
    returned_aft_entries: i64,
    rejected_aft_entries: i64,
    wire_recalls: i64,
}

#[derive(Serialize)]
struct ExceptionsResponse {
    window: String,
    since: DateTime<Utc>,
    exceptions: ExceptionCounts,
}

async fn count_since(
    pool: &DatabasePool,
    sql: &str,
    since: DateTime<Utc>,
) -> Result<i64, AppError> {
    sqlx::query_scalar::<_, i64>(sql)
        .bind(since)
        .fetch_one(pool)
        .await
        .map_err(AppError::Database)
}

/// Counts of the operational exceptions the ledger actually **records** over a
/// window: failed transactions, reversals, returned/rejected AFT entries, and
/// Lynx wire recalls. Declined authorizations and NSF-at-authorization now live
/// in the `decline_events` log — see the `/ops/declines` endpoint.
async fn ops_exceptions(
    _: AuthenticatedService,
    State(state): State<AppState>,
    Query(q): Query<WindowQuery>,
) -> Result<Json<ExceptionsResponse>, AppError> {
    let window = q.window.unwrap_or_else(|| "24h".to_string());
    let since = window_cutoff(&window)?;
    let p = &state.pool;
    let exceptions = ExceptionCounts {
        failed_transactions: count_since(
            p,
            "SELECT COUNT(*) FROM transactions WHERE status = 'failed' AND created_at >= $1",
            since,
        )
        .await?,
        reversals: count_since(
            p,
            "SELECT COUNT(*) FROM transaction_reversals WHERE created_at >= $1",
            since,
        )
        .await?,
        returned_aft_entries: count_since(
            p,
            "SELECT COUNT(*) FROM aft_entries WHERE status = 'returned' AND created_at >= $1",
            since,
        )
        .await?,
        rejected_aft_entries: count_since(
            p,
            "SELECT COUNT(*) FROM aft_entries WHERE status = 'rejected' AND created_at >= $1",
            since,
        )
        .await?,
        wire_recalls: count_since(
            p,
            "SELECT COUNT(*) FROM lynx_recalls WHERE created_at >= $1",
            since,
        )
        .await?,
    };
    Ok(Json(ExceptionsResponse {
        window,
        since,
        exceptions,
    }))
}

#[derive(sqlx::FromRow)]
struct HoldsRow {
    open_count: i64,
    open_amount: Decimal,
}

/// Authorization holds open **right now**. Point-in-time, NOT windowed: the
/// enclosing response's `window`/`since` do not apply to this field, so the
/// marker travels in the JSON rather than living only in a doc comment the
/// serializer drops. `as_of` is the instant it was read.
#[derive(Serialize)]
struct AuthorizationHolds {
    open_count: i64,
    open_amount: Decimal,
    as_of: DateTime<Utc>,
    basis: String,
}

const HOLDS_BASIS: &str = "point-in-time snapshot of holds open now (released_at \
    IS NULL); not scoped to the response window";

#[derive(Serialize, sqlx::FromRow)]
struct CardTxnGroup {
    transaction_type: String,
    status: String,
    count: i64,
    total: Decimal,
}

/// Cardholder engagement over the window: how many distinct cardholders made a
/// purchase, and how many made **exactly one** (a one-and-done / disengagement
/// signal). Both are windowed. `single_purchase` as a share of `active` is the
/// "used the card only once" rate the COO can compute via the `compute` tool.
#[derive(Serialize)]
struct CardholderEngagement {
    active: i64,
    single_purchase: i64,
}

/// Card-authorization outcome rates over the window. `approved` counts the
/// approved-auth holds (each approved authorization inserts one `visa_auth:%`
/// hold; holds are soft-released, rows retained, so this is accurate regardless
/// of later release/capture); `declined`/`nsf_declined` come from
/// `decline_events`. Rates are `None` when there were no authorizations at all.
#[derive(Serialize)]
struct CardAuthRates {
    approved: i64,
    declined: i64,
    nsf_declined: i64,
    approval_rate: Option<f64>,
    decline_rate: Option<f64>,
    nsf_rate: Option<f64>,
}

#[derive(Serialize)]
struct CardsResponse {
    window: String,
    since: DateTime<Utc>,
    /// Point-in-time (not windowed): authorization holds currently open. The
    /// `as_of`/`basis` fields carry that caveat in the payload itself.
    authorization_holds: AuthorizationHolds,
    /// Card-tagged transactions (`product = 'card'`) over the window.
    card_transactions: Vec<CardTxnGroup>,
    /// Distinct vs one-and-done cardholders (by `card_purchase`) over the window.
    cardholders: CardholderEngagement,
    /// Approval / decline / NSF rates over the window (from `decline_events` +
    /// retained approved-auth holds).
    rates: CardAuthRates,
}

/// Observable card operations: currently-open authorization holds (a now
/// snapshot), card-tagged transactions grouped by type and status over the
/// window, and approval/decline/NSF **rates** computed from the `decline_events`
/// log plus the retained approved-authorization holds.
async fn ops_cards(
    _: AuthenticatedService,
    State(state): State<AppState>,
    Query(q): Query<WindowQuery>,
) -> Result<Json<CardsResponse>, AppError> {
    let window = q.window.unwrap_or_else(|| "24h".to_string());
    let since = window_cutoff(&window)?;

    let holds = sqlx::query_as::<_, HoldsRow>(
        "SELECT COUNT(*) AS open_count, COALESCE(SUM(amount), 0) AS open_amount
         FROM account_holds
         WHERE released_at IS NULL",
    )
    .fetch_one(&state.pool)
    .await
    .map_err(AppError::Database)?;
    let authorization_holds = AuthorizationHolds {
        open_count: holds.open_count,
        open_amount: holds.open_amount,
        as_of: Utc::now(),
        basis: HOLDS_BASIS.to_string(),
    };

    let card_transactions = sqlx::query_as::<_, CardTxnGroup>(
        "SELECT transaction_type,
                status::text AS status,
                COUNT(*) AS count,
                COALESCE(SUM(amount), 0) AS total
         FROM transactions
         WHERE product = 'card' AND created_at >= $1
         GROUP BY transaction_type, status
         ORDER BY transaction_type, status",
    )
    .bind(since)
    .fetch_all(&state.pool)
    .await
    .map_err(AppError::Database)?;

    // A card_purchase is stamped with the cardholder as `initiated_by`, so
    // distinct/one-and-done cardholders come straight off the transactions table.
    let active_cardholders = count_since(
        &state.pool,
        "SELECT COUNT(DISTINCT initiated_by) FROM transactions
         WHERE transaction_type = 'card_purchase' AND created_at >= $1",
        since,
    )
    .await?;
    let single_purchase = count_since(
        &state.pool,
        "SELECT COUNT(*) FROM (
            SELECT initiated_by FROM transactions
            WHERE transaction_type = 'card_purchase' AND created_at >= $1
            GROUP BY initiated_by HAVING COUNT(*) = 1) t",
        since,
    )
    .await?;

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
    let rate = |n: i64| {
        if total > 0 {
            Some(n as f64 / total as f64)
        } else {
            None
        }
    };
    let rates = CardAuthRates {
        approved,
        declined,
        nsf_declined,
        approval_rate: rate(approved),
        decline_rate: rate(declined),
        nsf_rate: rate(nsf_declined),
    };

    Ok(Json(CardsResponse {
        window,
        since,
        authorization_holds,
        card_transactions,
        cardholders: CardholderEngagement {
            active: active_cardholders,
            single_purchase,
        },
        rates,
    }))
}

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

fn declines_map(
    rows: Vec<DeclineGroupRow>,
) -> (
    std::collections::BTreeMap<String, DeclineBucket>,
    i64,
    Decimal,
) {
    let mut m = std::collections::BTreeMap::new();
    let (mut tc, mut ta) = (0i64, Decimal::ZERO);
    for r in rows {
        tc += r.count;
        ta += r.amount;
        m.insert(
            r.key,
            DeclineBucket {
                count: r.count,
                amount: r.amount,
            },
        );
    }
    (m, tc, ta)
}

/// Declines over the window, grouped by category and by channel. The fraud
/// bucket (`reason_category='risk'`) is folded into `other` in SQL, so no fraud
/// signal ever leaves the bank. Pairs with `ops_cards`'s rates for card-approval
/// context; the other channels give NSF/limit visibility the rails otherwise hide.
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
    Ok(Json(DeclinesResponse {
        window,
        since,
        total_count,
        total_amount,
        by_category,
        by_channel,
    }))
}

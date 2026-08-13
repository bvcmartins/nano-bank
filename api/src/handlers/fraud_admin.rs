//! Drains the agent-denial outbox to the fraud engine.
//!
//! Every `agent_actions` row that is not `allowed` is mirrored into
//! `agent_denial_outbox` by the same statement that writes the audit (the CTE in
//! `policy.rs`), so the telemetry and the record it describes commit together.
//! This module is the other end: it claims undelivered rows and POSTs them to
//! the engine's `/v1/outcomes`.
//!
//! Why the bank pushes rather than the engine pulling: the engine has no access
//! to this database, and never will — the integration is HTTP-only by design.
//!
//! The API runs zero background workers by design, so the drain is an admin
//! endpoint poked on a schedule (see `k8s/fraud-denial-drainer-cronjob.yaml`),
//! the same shape as the Interac notification drainer — and now literally the
//! same claim, which both take from [`crate::outbox::OutboxClaim`].

use axum::{
    extract::{Path, State},
    response::Json,
    routing::{get, post},
    Router,
};
use uuid::Uuid;

use crate::config::database::DatabasePool;
use crate::errors::AppError;
use crate::handlers::AppState;
use crate::middleware::auth::AuthenticatedService;
use crate::outbox::OutboxClaim;

/// Attempts before a denial is dead-lettered: left undelivered with its
/// `last_delivery_error`, and no longer claimed.
const MAX_DELIVERY_ATTEMPTS: i32 = 5;
/// Rows claimed per flush — bounds one admin call's work.
const FLUSH_BATCH: i64 = 100;
/// Delivered rows are kept this long for debugging, then purged.
const DELIVERED_RETENTION_DAYS: i32 = 7;
/// Undelivered rows are kept longer, counted from creation and **regardless of
/// attempt count**. Dead-lettered rows are evidence that delivery is broken and
/// deleting them quickly would hide the outage; rows that were never attempted
/// at all (the `backend = "off"` default) are the same problem seen from the
/// other side. One window covers both, and covers the rows in between — a
/// partly-attempted row this old means nothing is draining either.
///
/// It is longer than the delivered window on purpose: enabling the backend
/// after a break should find a recent backlog to flush, not a hole.
const UNDELIVERED_RETENTION_DAYS: i32 = 30;

pub fn fraud_admin_routes() -> Router<AppState> {
    Router::new()
        .route("/admin/flush-denials", post(flush_denials))
        .route(
            "/admin/transactions/:transaction_id/fraud-link",
            get(fraud_link),
        )
        .route(
            "/admin/rails/:rail/:rail_id/fraud-link",
            get(fraud_link_rail),
        )
}

/// The engine's identifiers for one money row, where the bank persisted them.
///
/// **A null is not evidence that no decision exists** — see [`fraud_link`] for
/// the two different states it collapses.
#[derive(serde::Serialize)]
struct FraudLinkResponse {
    transaction_id: Uuid,
    operation_id: Option<Uuid>,
    decision_id: Option<Uuid>,
    /// Screening failed open: the engine was unreachable and the movement
    /// proceeded anyway. The decision may not exist engine-side, so a caller
    /// joining on `operation_id` should expect a miss rather than treat one as
    /// an error.
    failed_open: bool,
}

/// Look up the fraud engine's `operation_id` for a bank transaction.
///
/// **Why this exists.** The engine joins ground truth to decisions on
/// `outcome_events.operation_id = decisions.operation_id`; its `decisions` table
/// has no `transaction_id` column, because the bank mints that inside its own
/// transaction *after* the fraud check. So `operation_id` is the only key that
/// can attach an outcome to a decision — and until now it never left the bank.
/// It was written to `transactions.metadata.fraud` and read by nobody, so no
/// decision could be labelled and the engine's training-set export returned zero
/// rows however much traffic ran (#46).
///
/// **Why an endpoint rather than a response field.** `operation_id` and
/// `decision_id` are fraud-engine internals. Putting them on
/// `TransactionResponse` would publish them to the customer plane — the
/// disclosure concern #34's review raised about echoing bank internals. A
/// service-token route keeps the whole concern on the service plane behind one
/// auth check, instead of a field whose presence depends on who is asking.
///
/// **Not customer-scoped**, deliberately: the caller is the fraud operator, not
/// an account holder.
///
/// # What a null means — three states, and this response distinguishes two
///
/// | State | Response | Reachable? |
/// |---|---|---|
/// | Screened, link persisted | ids present | yes |
/// | Never screened (`backend = "off"`) | nulls | nothing to reach |
/// | **Screened, link not persisted** | **nulls** | **no — but a decision exists** |
///
/// The third row was the trap while it lasted. It no longer applies to the
/// bank's own paths: `transactions.rs` (deposit/withdrawal/transfer) stamp the
/// linkage inline, and the rails (interac/lynx via #53, aft via #57, cards via
/// #56) now carry it onto their money row too. So for a transaction the bank
/// wrote and screened, a null here means "not screened", not "not written down".
///
/// It survives only for a caller holding a **rail** id (`etransfer_id`,
/// `wire_id`, an aft `entry_id`) rather than the money `transaction_id`: this
/// route can't take those. [`fraud_link_rail`] does — it maps the rail id to the
/// money row and then reads exactly what this route reads.
///
/// Even so, **a null is not proof no decision exists** in the general case: a
/// screening that ran with `backend = "off"` writes no `fraud` key, and the bank
/// records nowhere whether a movement was screened, so the two null states
/// aren't distinguished here and deliberately aren't guessed at.
async fn fraud_link(
    State(state): State<AppState>,
    Path(transaction_id): Path<Uuid>,
    _svc: AuthenticatedService,
) -> Result<Json<FraudLinkResponse>, AppError> {
    fraud_link_for(&state.pool, transaction_id)
        .await?
        .map(Json)
        .ok_or_else(|| AppError::NotFound("transaction not found".to_string()))
}

/// Read the engine linkage off a money row's `metadata.fraud`. `Ok(None)` means
/// no such transaction (the caller renders a 404); `Ok(Some(_))` is a 200, with
/// nulls when the row carries no `fraud` key. Shared by the transaction route
/// ([`fraud_link`]) and the rail route ([`fraud_link_rail`]) so both answer
/// identically once a `transaction_id` is in hand.
async fn fraud_link_for(
    pool: &DatabasePool,
    transaction_id: Uuid,
) -> Result<Option<FraudLinkResponse>, AppError> {
    // `metadata` is nullable, so the outer Option is "no such transaction" and
    // the inner one is "transaction exists, metadata NULL".
    let row: Option<Option<serde_json::Value>> =
        sqlx::query_scalar("SELECT metadata FROM transactions WHERE transaction_id = $1")
            .bind(transaction_id)
            .fetch_optional(pool)
            .await?;
    let Some(metadata) = row else {
        return Ok(None);
    };

    let fraud = metadata.as_ref().and_then(|m| m.get("fraud"));
    let uuid_at = |key: &str| -> Option<Uuid> {
        fraud
            .and_then(|f| f.get(key))
            .and_then(serde_json::Value::as_str)
            .and_then(|s| s.parse().ok())
    };

    Ok(Some(FraudLinkResponse {
        transaction_id,
        operation_id: uuid_at("operation_id"),
        decision_id: uuid_at("decision_id"),
        failed_open: fraud
            .and_then(|f| f.get("failed_open"))
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
    }))
}

/// Resolve a **rail movement's own id** to the screened decision linkage.
///
/// A caller that drove a rail (the fraud operator's tooling, the world-model
/// label loop) holds the rail's id — `etransfer_id`, `wire_id`, an aft
/// `entry_id` — not the money `transaction_id` the linkage sits on. Each rail
/// screens once and stamps `metadata.fraud` on exactly one money row (#53/#57/
/// #56); this maps the rail id to that row, then defers to [`fraud_link_for`],
/// so the answer is identical to the transaction route.
///
/// - unknown `rail` → 400.
/// - unknown `rail_id`, or its money row not written yet (e.g. an aft entry
///   before settlement) → 404.
/// - otherwise 200, with nulls when that row wasn't screened — same honest
///   semantics as [`fraud_link`].
///
/// Cards are intentionally not here: they resolve through
/// `transactions.metadata->>'auth_id'` rather than an FK column, and no consumer
/// needs them yet.
async fn fraud_link_rail(
    State(state): State<AppState>,
    Path((rail, rail_id)): Path<(String, Uuid)>,
    _svc: AuthenticatedService,
) -> Result<Json<FraudLinkResponse>, AppError> {
    // The table/column is chosen from a fixed set (never caller input); `rail_id`
    // is always a bound parameter.
    let resolve = match rail.as_str() {
        "interac" => "SELECT hold_transaction_id FROM interac_etransfers WHERE etransfer_id = $1",
        "lynx" => "SELECT settlement_transaction_id FROM lynx_wires WHERE wire_id = $1",
        "aft" => "SELECT settle_transaction_id FROM aft_entries WHERE entry_id = $1",
        other => return Err(AppError::BadRequest(format!("unknown rail: {other}"))),
    };

    // `flatten()` collapses both "no such rail row" and "row exists but its money
    // transaction is NULL" (unsettled) into the same 404 — neither can be linked.
    let txn_id: Option<Uuid> = sqlx::query_scalar(resolve)
        .bind(rail_id)
        .fetch_optional(&state.pool)
        .await?
        .flatten();
    let txn_id = txn_id.ok_or_else(|| {
        AppError::NotFound(format!("no screened transaction for {rail} {rail_id}"))
    })?;

    fraud_link_for(&state.pool, txn_id)
        .await?
        .map(Json)
        .ok_or_else(|| AppError::NotFound("transaction not found".to_string()))
}

#[derive(sqlx::FromRow)]
struct ClaimedDenial {
    outbox_id: Uuid,
    payload: serde_json::Value,
}

/// Drain the agent-denial outbox (admin plane, service token).
///
/// The claim is an atomic `delivery_attempts += 1` under `FOR UPDATE SKIP
/// LOCKED`, so concurrent drainers or multiple API replicas never grab the same
/// row, and a claim that dies mid-send costs one attempt rather than stranding
/// an in-flight state.
///
/// **Delivery is at-least-once**, and that is safe here precisely because the
/// payload carries `event_key` derived from `action_id`: the engine's outcome
/// ingestion is idempotent on it, so a redelivery collapses into the original
/// event instead of double-counting a denial.
async fn flush_denials(
    State(state): State<AppState>,
    _svc: AuthenticatedService,
) -> Result<Json<serde_json::Value>, AppError> {
    // Retention runs first, and unconditionally. The table grows fastest in
    // exactly the configuration that never reaches the delivery loop below —
    // `backend = "off"` is the default, and every denial still lands in the
    // outbox — so a purge that only runs when draining is enabled is a purge
    // that never runs on the deployments that need it.
    let purged = purge_expired(&state.pool).await?;

    // With screening off there is no engine to talk to. Skip without claiming:
    // claiming would burn the retry budget of every row against a backend
    // nobody asked us to call, dead-lettering the lot before it is ever enabled.
    if state.fraud.backend() == "off" {
        let pending: i64 =
            sqlx::query_scalar("SELECT count(*) FROM agent_denial_outbox WHERE delivered = FALSE")
                .fetch_one(&state.pool)
                .await?;
        return Ok(Json(serde_json::json!({
            "skipped": pending,
            "purged": purged,
            "reason": "fraud backend off",
        })));
    }

    let claimed = sqlx::query_as::<_, ClaimedDenial>(
        &OutboxClaim {
            table: "agent_denial_outbox",
            id_column: "outbox_id",
            returning: "outbox_id, payload",
        }
        .sql(),
    )
    .bind(MAX_DELIVERY_ATTEMPTS)
    .bind(FLUSH_BATCH)
    .fetch_all(&state.pool)
    .await?;

    let claimed_count = claimed.len() as i64;
    let mut delivered = 0i64;
    let mut failed = 0i64;

    for row in claimed {
        match state.fraud.report_denial(&row.payload).await {
            Ok(()) => {
                sqlx::query(
                    "UPDATE agent_denial_outbox \
                     SET delivered = TRUE, delivered_at = CURRENT_TIMESTAMP, \
                         last_delivery_error = NULL \
                     WHERE outbox_id = $1",
                )
                .bind(row.outbox_id)
                .execute(&state.pool)
                .await?;
                delivered += 1;
            }
            Err(err) => {
                // Leave delivered = FALSE (the attempt is already counted): it
                // retries next flush until the budget is spent, then dead-letters.
                sqlx::query(
                    "UPDATE agent_denial_outbox SET last_delivery_error = $2 \
                     WHERE outbox_id = $1",
                )
                .bind(row.outbox_id)
                .bind(err.to_string())
                .execute(&state.pool)
                .await?;
                failed += 1;
            }
        }
    }

    Ok(Json(serde_json::json!({
        "claimed": claimed_count,
        "delivered": delivered,
        "failed": failed,
        "purged": purged,
    })))
}

/// Drop outbox rows past their retention window. The Interac outbox has no
/// purge and grows forever; this one must have it.
///
/// Two predicates, split on the only thing that changes the window: whether the
/// row ever reached the engine. Delivered rows are debugging residue and go
/// early; undelivered ones are kept the full window from creation whatever
/// their attempt count, because "never attempted", "mid-retry" and
/// "dead-lettered" are all the same condition — nothing is draining — and
/// deserve the same grace period.
async fn purge_expired(pool: &DatabasePool) -> Result<u64, sqlx::Error> {
    Ok(sqlx::query(
        "DELETE FROM agent_denial_outbox \
         WHERE (delivered = TRUE \
                AND delivered_at < CURRENT_TIMESTAMP - ($1 || ' days')::interval) \
            OR (delivered = FALSE \
                AND created_at < CURRENT_TIMESTAMP - ($2 || ' days')::interval)",
    )
    .bind(DELIVERED_RETENTION_DAYS.to_string())
    .bind(UNDELIVERED_RETENTION_DAYS.to_string())
    .execute(pool)
    .await?
    .rows_affected())
}

//! Autonomous COO operational levers.
//!
//! These are the service-plane endpoints the COO pulls on its own judgement —
//! there is no human in the loop. Each lever is *self-verifying*: before it acts
//! it re-checks a **deterministic** precondition against live bank state (an
//! open non-empty batch, actually-expired transfers, actually-stale wires,
//! actually-undelivered notifications). The model cannot argue past that check —
//! if the precondition is false the lever refuses, no matter what the agent
//! "believes".
//!
//! Either way — executed OR refused — the attempt is written to the
//! tamper-evident `agent_action_ledger` (hash-chained, append-only, immutable
//! server-side; see `src/core/tables/14_agent_action_ledger.sql`). No agent can
//! read or write that ledger except through this narrow, audited path, so every
//! autonomous action the COO takes leaves a trail no agent can alter.
//!
//! The underlying money/state movement reuses each rail's admin handler logic
//! (the `*_inner` functions in `aft.rs` / `interac.rs` / `lynx.rs`), so a COO
//! lever and the equivalent human admin call do exactly the same thing.

use axum::{extract::State, routing::post, Json, Router};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::config::database::DatabasePool;
use crate::errors::AppError;
use crate::handlers::{aft, interac, lynx, AppState};
use crate::middleware::auth::AuthenticatedService;

pub fn ops_lever_routes() -> Router<AppState> {
    Router::new()
        .route("/cut-aft-batch", post(cut_aft_batch))
        .route("/sweep-expired-etransfers", post(sweep_expired_etransfers))
        .route("/reject-stale-wires", post(reject_stale_wires))
        .route("/flush-notifications", post(flush_notifications))
}

/// Append one entry to the tamper-evident agent-action ledger (actor `coo`).
/// The JSON is passed as text and cast to `jsonb` server-side, mirroring the
/// CFO's Python writer. A failure here is surfaced to the caller — an
/// autonomous action must never land without its audit row.
async fn audit(
    pool: &DatabasePool,
    action: &str,
    params: &Value,
    effect: &Value,
) -> Result<(), AppError> {
    sqlx::query("SELECT append_agent_action('coo', $1, $2::jsonb, $3::jsonb)")
        .bind(action)
        .bind(params.to_string())
        .bind(effect.to_string())
        .execute(pool)
        .await?;
    Ok(())
}

fn executed(effect: Value) -> Value {
    json!({ "outcome": "executed", "effect": effect })
}

fn refused(reason: &str) -> Value {
    json!({ "outcome": "refused", "reason": reason })
}

/// Cut (submit) the single open outbound AFT batch — but only if it actually
/// has entries. An empty or already-cut batch is a no-op refusal.
async fn cut_aft_batch(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
) -> Result<Json<Value>, AppError> {
    let params = json!({});
    // self-verify: an open OUTBOUND batch with at least one entry.
    let open: Option<(Uuid, i32)> = sqlx::query_as(
        "SELECT batch_id, entry_count FROM aft_batches \
         WHERE status='open' AND direction='outbound' LIMIT 1",
    )
    .fetch_optional(&state.pool)
    .await?;

    let outcome = match open {
        Some((batch_id, entry_count)) if entry_count > 0 => {
            aft::submit_batch_inner(&state, batch_id).await?;
            executed(json!({ "batch_id": batch_id, "entry_count": entry_count }))
        }
        _ => refused("no open outbound AFT batch with entries to cut"),
    };
    audit(&state.pool, "cut_aft_batch", &params, &outcome).await?;
    Ok(Json(outcome))
}

/// Sweep expired (unclaimed) Interac e-Transfers, refunding the senders — only
/// if at least one is actually past its expiry.
async fn sweep_expired_etransfers(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
) -> Result<Json<Value>, AppError> {
    let params = json!({});
    let due: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM interac_etransfers \
         WHERE status='available' AND expires_at < CURRENT_TIMESTAMP",
    )
    .fetch_one(&state.pool)
    .await?;

    let outcome = if due > 0 {
        let effect = interac::sweep_expired_inner(&state).await?;
        executed(effect)
    } else {
        refused("no expired e-Transfers to sweep")
    };
    audit(&state.pool, "sweep_expired_etransfers", &params, &outcome).await?;
    Ok(Json(outcome))
}

/// Reject Lynx wires stuck in `sent` past the stale threshold, refunding the
/// senders — only if at least one is actually stale.
async fn reject_stale_wires(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
) -> Result<Json<Value>, AppError> {
    let params = json!({ "stale_minutes": lynx::stale_minutes(&state) });
    let due: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM lynx_wires \
         WHERE status='sent' AND sent_at < CURRENT_TIMESTAMP - make_interval(mins => $1)",
    )
    .bind(lynx::stale_minutes(&state))
    .fetch_one(&state.pool)
    .await?;

    let outcome = if due > 0 {
        let effect = lynx::admin_reject_stale_inner(&state).await?;
        executed(effect)
    } else {
        refused("no stale wires to reject")
    };
    audit(&state.pool, "reject_stale_wires", &params, &outcome).await?;
    Ok(Json(outcome))
}

/// Drain the Interac notification outbox — only if there is at least one
/// undelivered notification still within its retry budget.
async fn flush_notifications(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
) -> Result<Json<Value>, AppError> {
    let params = json!({});
    let pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM interac_notifications \
         WHERE delivered = FALSE AND delivery_attempts < $1",
    )
    .bind(interac::MAX_DELIVERY_ATTEMPTS)
    .fetch_one(&state.pool)
    .await?;

    let outcome = if pending > 0 {
        let effect = interac::flush_notifications_inner(&state).await?;
        executed(effect)
    } else {
        refused("no undelivered notifications to flush")
    };
    audit(&state.pool, "flush_notifications", &params, &outcome).await?;
    Ok(Json(outcome))
}

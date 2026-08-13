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

/// Record the attempt in the tamper-evident agent-action ledger (actor `coo`)
/// and return the outcome to the caller. The JSON is passed as text and cast to
/// `jsonb` server-side, mirroring the CFO's Python writer.
///
/// The action already ran (or was deterministically refused) before we get here;
/// the levers reuse each rail's `*_inner` handler, which commits in its own
/// transaction, and the notification drainer deliberately commits per item under
/// `FOR UPDATE SKIP LOCKED` — so the audit cannot share one transaction with the
/// action. We make the failure modes honest instead:
///
/// - **Refused** (nothing executed): if the audit write fails, surface it (`?`).
///   Nothing moved, so a retry is safe and correct.
/// - **Executed** (money/state already moved): the action is the primary effect
///   and it is real. Reporting it as a 500 because a *secondary* bookkeeping
///   write failed would be a lie that drives a retry — which self-verify then
///   turns into a misleading "refused" entry, so the real action would vanish
///   from the ledger twice over. Instead we log the complete record at ERROR
///   (recoverable — actor/action/params/effect) and still return the true
///   outcome. The ledger gap is loud in the logs, never silent.
async fn finalize(
    pool: &DatabasePool,
    action: &str,
    params: &Value,
    outcome: Value,
) -> Result<Json<Value>, AppError> {
    let res = sqlx::query("SELECT append_agent_action('coo', $1, $2::jsonb, $3::jsonb)")
        .bind(action)
        .bind(params.to_string())
        .bind(outcome.to_string())
        .execute(pool)
        .await;

    if let Err(e) = res {
        let executed = outcome.get("outcome").and_then(Value::as_str) == Some("executed");
        if !executed {
            return Err(AppError::from(e)); // nothing ran; fail the call, safe to retry
        }
        // The action landed. Never misreport it; make the missing audit row loud.
        tracing::error!(
            error = %e, actor = "coo", action,
            params = %params, effect = %outcome,
            "UNAUDITED EXECUTED ACTION: agent-action ledger write failed after the \
             action committed — record preserved here for recovery"
        );
    }
    Ok(Json(outcome))
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
    finalize(&state.pool, "cut_aft_batch", &params, outcome).await
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
    finalize(&state.pool, "sweep_expired_etransfers", &params, outcome).await
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
    finalize(&state.pool, "reject_stale_wires", &params, outcome).await
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
    finalize(&state.pool, "flush_notifications", &params, outcome).await
}

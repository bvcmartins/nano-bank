//! The shared screening gate every money-movement handler calls BEFORE its
//! database transaction opens. Owns: operation-id minting, session-context
//! recovery, the decision→error mapping, and the fail-open/fail-closed matrix.
//! Callers get back a [`FraudLink`] to stamp into their money row's metadata —
//! or an `AppError` that aborts the movement before any DB write.
//!
//! Auditing a refusal is the caller's job, not the gate's: the agent plane
//! already records every failed transfer with its reason code, and a second
//! writer here produced two contradictory `agent_actions` rows. Any future
//! agent-reachable money path must keep that catch-all.

use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use uuid::Uuid;

use crate::errors::AppError;
use crate::handlers::AppState;

use super::{FraudAction, FraudAgentCtx, FraudCheckError, FraudRequest, FraudSessionCtx};

/// Everything a call site knows about the movement being screened.
pub(crate) struct ScreenInput<'a> {
    pub kind: &'static str,
    pub amount: Decimal,
    pub customer_id: Uuid,
    pub from_account_id: Uuid,
    pub to_account_id: Option<Uuid>,
    pub payee_handle: Option<&'a str>,
    pub description: Option<&'a str>,
    pub external_reference: Option<&'a str>,
    pub merchant: Option<&'a str>,
    /// The caller's idempotency key, when one exists: a bank retry of the same
    /// attempt must replay the same engine decision.
    pub idempotency_key: Option<&'a str>,
    /// web | mobile_app | card_network — overridden to agentic_branch whenever
    /// `agent` is Some (agency is spec-derived, never auth-plane-derived).
    pub channel: &'static str,
    pub session_id: Option<Uuid>,
    pub agent: Option<FraudAgentCtx>,
}

/// What a money-movement caller knows about its channel — passed down into
/// `execute_transfer` (and used directly by the single-purpose handlers).
#[derive(Debug, Clone, Copy)]
pub(crate) struct Screening {
    /// web | mobile_app | card_network (agentic_branch is derived from the
    /// presence of agent context, never passed).
    pub channel: &'static str,
    pub session_id: Option<Uuid>,
    /// Step-up flows only: seconds between park and customer approval.
    pub approval_latency_seconds: Option<f64>,
    /// Distinguishes semantically different screenings of the SAME caller
    /// idempotency key. The step-up flow screens twice — the agent's original
    /// ask (pre-park) and the customer-approved execution — and without a
    /// scope the second would replay the first's decision, never evaluating
    /// the cap-override context.
    pub screen_scope: Option<&'static str>,
}

impl Screening {
    pub fn customer(session_id: Option<Uuid>) -> Self {
        Self {
            channel: "web",
            session_id,
            approval_latency_seconds: None,
            screen_scope: None,
        }
    }
}

/// The linkage a caller stamps into `transactions.metadata.fraud`.
#[derive(Debug, Clone)]
pub(crate) struct FraudLink {
    pub operation_id: Uuid,
    pub decision_id: Option<Uuid>,
    pub failed_open: bool,
    /// False in off-mode: callers skip metadata stamping so the bank's
    /// money rows are byte-identical to pre-port behavior until opted in.
    pub screened: bool,
    /// Deferred fail-open rescore payload — `Some` only when this screening
    /// failed open. The caller fires it via [`FraudLink::settle_rescore`] once
    /// the movement's outcome is known, so the engine learns whether the money
    /// actually moved rather than a guess made before the DB transaction.
    rescore: Option<Box<FraudRequest>>,
}

impl FraudLink {
    /// The JSON blob stamped into `transactions.metadata.fraud` — the audit
    /// join path between bank money rows and engine decision rows.
    pub fn metadata(&self) -> serde_json::Value {
        serde_json::json!({
            "operation_id": self.operation_id,
            "decision_id": self.decision_id,
            "failed_open": self.failed_open,
        })
    }

    /// Fire the deferred fail-open rescore now that the movement's outcome is
    /// known. A no-op unless this screening failed open. Fire-and-forget: it
    /// feeds only the engine's post-hoc case-opening (a bad verdict on money
    /// that *did* move) and must never affect the request path — adapter errors
    /// are swallowed. Callers pass `executed = true` after the movement's
    /// transaction commits; a movement that aborted before commit simply drops
    /// the link, so nothing is rescored (correctly — no money moved).
    pub(crate) fn settle_rescore(&self, state: &AppState, executed: bool) {
        if let Some(request) = &self.rescore {
            let fraud = state.fraud.clone();
            let request = (**request).clone();
            tokio::spawn(async move { fraud.rescore(request, executed).await });
        }
    }
}

async fn session_context(state: &AppState, session_id: Option<Uuid>) -> Option<FraudSessionCtx> {
    let session_id = session_id?;
    type Row = (
        String,
        Option<String>,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<DateTime<Utc>>,
    );
    let row: Option<Row> = sqlx::query_as(
        "SELECT host(ip_address), user_agent, device_fingerprint, created_at, last_activity_at \
         FROM user_sessions WHERE session_id = $1 AND is_active = TRUE",
    )
    .bind(session_id)
    .fetch_optional(&state.pool)
    .await
    .ok()
    .flatten();
    row.map(
        |(ip_address, user_agent, device_fingerprint, created_at, last_activity_at)| {
            FraudSessionCtx {
                session_id,
                ip_address,
                user_agent,
                device_fingerprint,
                session_created_at: created_at,
                last_activity_at,
            }
        },
    )
}

pub(crate) async fn screen(
    state: &AppState,
    input: ScreenInput<'_>,
) -> Result<FraudLink, AppError> {
    let operation_id = Uuid::new_v4();
    if state.fraud.backend() == "off" {
        return Ok(FraudLink {
            operation_id,
            decision_id: None,
            failed_open: false,
            screened: false,
            rescore: None,
        });
    }

    let idempotency_key = match input.idempotency_key {
        Some(key) => format!("txn-{}-{}", input.customer_id, key),
        None => format!("op-{operation_id}"),
    };
    let request = FraudRequest {
        operation_id,
        idempotency_key,
        kind: input.kind,
        amount: input.amount,
        from_account_id: input.from_account_id,
        to_account_id: input.to_account_id,
        payee_handle: input.payee_handle.map(str::to_string),
        description: input.description.map(str::to_string),
        external_reference: input.external_reference.map(str::to_string),
        merchant: input.merchant.map(str::to_string),
        customer_id: input.customer_id,
        initiated_via: if input.agent.is_some() {
            "agentic_branch"
        } else {
            input.channel
        },
        agent: input.agent.clone(),
        session: session_context(state, input.session_id).await,
        // The security boundary, and the reason it lives here rather than in
        // the middleware that carried the header: this is where the outbound
        // request is assembled, so this is where a reviewer looks for what the
        // caller is allowed to influence. Off by default — see
        // config::FraudSettings::accept_simulated_time.
        requested_at: super::simulated_time::decision_instant(
            state.settings.fraud.accept_simulated_time,
            super::simulated_time::supplied(),
        ),
    };

    match state.fraud.assess(&request).await {
        Ok(decision) => {
            tracing::debug!(
                operation_id = %operation_id,
                engine_mode = %decision.engine_mode,
                "fraud decision received"
            );
            match decision.action {
                FraudAction::Allow => Ok(FraudLink {
                    operation_id,
                    decision_id: Some(decision.decision_id),
                    failed_open: false,
                    screened: true,
                    rescore: None,
                }),
                // The agent-plane audit for these is written by the caller's
                // catch-all (handlers/agent_api.rs), which records EVERY failed
                // agent transfer with its reason code. Auditing here as well wrote
                // a second, contradictory row.
                FraudAction::Block => Err(AppError::TransactionDeclined),
                // hold_review today; challenge/delay_and_warn collapse here until
                // the bank grows a challenge UX (integration phase 2).
                FraudAction::HoldReview | FraudAction::Challenge | FraudAction::DelayAndWarn => {
                    Err(AppError::TransactionUnderReview(
                        decision.message_for_customer.unwrap_or_else(|| {
                            "This transaction requires additional review before it can be completed."
                                .to_string()
                        }),
                    ))
                }
            }
        }
        Err(e) => {
            if let FraudCheckError::Backend { status, body } = &e {
                // Contract bug on our side — make it impossible to miss.
                tracing::error!(status, body, "fraud engine rejected the request (bank bug)");
            }
            if input.amount <= state.settings.fraud.fail_closed_above {
                // FAIL OPEN: the movement proceeds; the engine assesses it
                // post-hoc as soon as it can and opens a case on a bad verdict.
                tracing::warn!(
                    operation_id = %operation_id,
                    kind = input.kind,
                    amount = %input.amount,
                    error = %e,
                    "fraud check failed open"
                );
                // The movement hasn't run yet (this is before the DB tx opens),
                // so we can't know `executed` here. Defer the rescore into the
                // link; the caller fires it after the movement's outcome is
                // known (see FraudLink::settle_rescore).
                Ok(FraudLink {
                    operation_id,
                    decision_id: None,
                    failed_open: true,
                    screened: true,
                    rescore: Some(Box::new(request)),
                })
            } else {
                // FAIL CLOSED: above the risk threshold no money moves blind.
                tracing::error!(
                    operation_id = %operation_id,
                    kind = input.kind,
                    amount = %input.amount,
                    error = %e,
                    "fraud check failed closed"
                );
                Err(AppError::ServiceUnavailable(
                    "transaction cannot be processed right now — please retry".to_string(),
                ))
            }
        }
    }
}

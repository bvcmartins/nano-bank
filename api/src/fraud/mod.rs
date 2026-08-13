//! The **FraudCheck port**: nano-bank's backend-agnostic interface to the fraud
//! engine, mirroring the Ledger port pattern. Two adapters — the HTTP engine
//! (`nano-bank-fraud-engine`, :8092) and a no-op — selected at startup by
//! `[fraud] backend` (`NANO_BANK__FRAUD__BACKEND`), default **off** so the bank
//! runs unchanged until screening is opted in.
//!
//! Every customer-initiated money movement calls [`gate::screen`] BEFORE its
//! database transaction opens (never hold row locks across a network call).
//! The engine answers with an action, never a score; declines surface as the
//! opaque `TRANSACTION_DECLINED` / `TRANSACTION_UNDER_REVIEW` errors.

pub mod engine;
pub mod gate;
pub mod noop;
pub mod simulated_time;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use uuid::Uuid;

/// The engine's verdict vocabulary. `Challenge`/`DelayAndWarn` are contract-
/// ready but collapse to the under-review treatment until the bank has a
/// challenge UX (integration phase 2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FraudAction {
    Allow,
    Block,
    HoldReview,
    Challenge,
    DelayAndWarn,
}

#[derive(Debug, Clone)]
pub struct FraudDecision {
    pub decision_id: Uuid,
    pub action: FraudAction,
    pub engine_mode: String,
    pub message_for_customer: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum FraudCheckError {
    #[error("fraud engine timed out")]
    Timeout,
    #[error("fraud transport error: {0}")]
    Transport(String),
    /// The engine rejected the request itself (4xx) — a bank-side contract
    /// bug, logged loudly; treated like an outage by the failure matrix.
    #[error("fraud engine returned {status}: {body}")]
    Backend { status: u16, body: String },
}

/// Agency context forwarded to the engine. `cap_override`/`approval_latency`
/// describe the step-up flow: a parked over-cap agent transfer the customer
/// approved later (the one agent flow that carries a session).
#[derive(Debug, Clone)]
pub struct FraudAgentCtx {
    pub agent_id: Uuid,
    pub mandate_id: Uuid,
    pub cap_override: bool,
    pub approval_latency_seconds: Option<f64>,
}

/// Session context recovered from `user_sessions` by the caller's session id.
#[derive(Debug, Clone)]
pub struct FraudSessionCtx {
    pub session_id: Uuid,
    pub ip_address: String,
    pub user_agent: Option<String>,
    pub device_fingerprint: Option<String>,
    pub session_created_at: Option<DateTime<Utc>>,
    pub last_activity_at: Option<DateTime<Utc>>,
}

/// One money-movement attempt, in the engine's vocabulary (see the engine
/// repo's `api/openapi.yaml` DecisionRequest).
#[derive(Debug, Clone)]
pub struct FraudRequest {
    /// Bank-minted per-attempt id; the bank's transaction_id does not exist
    /// yet at screening time. Stamped into `transactions.metadata.fraud`.
    pub operation_id: Uuid,
    pub idempotency_key: String,
    /// transfer | deposit | withdrawal | card_authorize | interac_etransfer
    /// | aft_batch | lynx_transfer
    pub kind: &'static str,
    pub amount: Decimal,
    pub from_account_id: Uuid,
    pub to_account_id: Option<Uuid>,
    /// External destination for rails without an account UUID (Interac
    /// email/phone handle, AFT/Lynx counterparty reference).
    pub payee_handle: Option<String>,
    pub description: Option<String>,
    pub external_reference: Option<String>,
    pub merchant: Option<String>,
    pub customer_id: Uuid,
    /// web | mobile_app | agentic_branch | card_network
    pub initiated_via: &'static str,
    pub agent: Option<FraudAgentCtx>,
    pub session: Option<FraudSessionCtx>,
    /// The instant to measure this decision at, when the deployment accepts a
    /// caller-supplied one (`fraud.accept_simulated_time`, default off). `None`
    /// means the engine uses its own clock — the case for all real traffic.
    pub requested_at: Option<DateTime<Utc>>,
}

/// The fraud engine seen by nano-bank. Kept small: one synchronous assessment
/// plus the post-hoc rescore used after a fail-open.
#[async_trait]
pub trait FraudCheck: Send + Sync {
    /// Which backend this is ("engine" | "off"), for diagnostics and the
    /// off-mode fast path.
    fn backend(&self) -> &'static str;

    /// Assess one money movement. Budget: the adapter times out inside the
    /// caller's latency envelope; failures are handled by the gate's
    /// fail-open/fail-closed matrix, not by callers.
    async fn assess(&self, req: &FraudRequest) -> Result<FraudDecision, FraudCheckError>;

    /// Best-effort post-hoc assessment after a fail-open (the money already
    /// moved). Errors are swallowed by implementations — this must never
    /// affect a request path.
    async fn rescore(&self, req: FraudRequest, executed: bool);

    /// Deliver one agent-denial telemetry event (an action the BANK refused,
    /// so no engine decision exists for it) to the engine's outcome ingestion.
    ///
    /// Unlike [`rescore`] this returns a `Result`: it is called by the outbox
    /// drainer, which must know whether the row may be marked delivered. A
    /// swallowed error here would silently drop the row forever, which is the
    /// exact failure the outbox exists to prevent.
    async fn report_denial(&self, payload: &serde_json::Value) -> Result<(), FraudCheckError>;
}

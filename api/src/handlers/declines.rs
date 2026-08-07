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

//! HTTP adapter for the real fraud engine (`nano-bank-fraud-engine`, :8092).
//! Tight total timeout, bearer service token, and a small circuit breaker so a
//! dead engine costs one fast error instead of a full timeout per transaction.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use serde_json::json;

use super::{FraudAction, FraudCheck, FraudCheckError, FraudDecision, FraudRequest};

const BREAKER_THRESHOLD: u32 = 5;
const BREAKER_OPEN_SECS: u64 = 10;

/// Consecutive-failure circuit breaker, one per endpoint we call.
///
/// It is a type rather than a set of fields on the adapter because the two
/// endpoints have unrelated failure modes and unrelated urgency: `/v1/decisions`
/// is synchronous and gates money movement, `/v1/outcomes` is a background
/// drain of already-recorded history. Sharing one breaker between them couples
/// them in both directions — a slow outcomes drain would open the breaker in
/// front of live transactions, and a decisions outage would spend the outbox's
/// retry budget on rows that were never tried.
#[derive(Default)]
struct Breaker {
    consecutive_failures: AtomicU32,
    open_until: Mutex<Option<Instant>>,
}

impl Breaker {
    fn open(&self) -> bool {
        let mut open = self.open_until.lock().expect("breaker lock");
        match *open {
            Some(until) if Instant::now() < until => true,
            Some(_) => {
                // Half-open: let this request probe; a failure re-opens below.
                *open = None;
                false
            }
            None => false,
        }
    }

    fn record_failure(&self) {
        let failures = self.consecutive_failures.fetch_add(1, Ordering::Relaxed) + 1;
        if failures >= BREAKER_THRESHOLD {
            *self.open_until.lock().expect("breaker lock") =
                Some(Instant::now() + Duration::from_secs(BREAKER_OPEN_SECS));
        }
    }

    fn record_success(&self) {
        self.consecutive_failures.store(0, Ordering::Relaxed);
        *self.open_until.lock().expect("breaker lock") = None;
    }
}

pub struct EngineFraudCheck {
    base_url: String,
    token: String,
    http: reqwest::Client,
    /// Background telemetry gets its own client. `http` carries the synchronous
    /// decision budget (150ms by default) which is the caller's latency
    /// envelope, not a sane deadline for an outbox drain.
    telemetry_http: reqwest::Client,
    /// Guards `/v1/decisions`, the request path.
    decisions_breaker: Breaker,
    /// Guards `/v1/outcomes`, the outbox drain. Deliberately separate — see
    /// [`Breaker`].
    telemetry_breaker: Breaker,
}

impl EngineFraudCheck {
    pub fn new(
        base_url: impl Into<String>,
        token: impl Into<String>,
        timeout_ms: u64,
        outcomes_timeout_ms: u64,
    ) -> Self {
        Self {
            base_url: base_url.into(),
            token: token.into(),
            http: reqwest::Client::builder()
                .timeout(Duration::from_millis(timeout_ms))
                .connect_timeout(Duration::from_millis(timeout_ms.min(50)))
                .build()
                .expect("reqwest client"),
            telemetry_http: reqwest::Client::builder()
                .timeout(Duration::from_millis(outcomes_timeout_ms))
                .build()
                .expect("reqwest telemetry client"),
            decisions_breaker: Breaker::default(),
            telemetry_breaker: Breaker::default(),
        }
    }

    fn wire_body(req: &FraudRequest) -> serde_json::Value {
        json!({
            "idempotency_key": req.idempotency_key,
            "transaction": {
                "operation_id": req.operation_id,
                "type": req.kind,
                // string-decimal on the wire, never a float
                "amount": req.amount.to_string(),
                "currency": "CAD",
                "from_account_id": req.from_account_id,
                "to_account_id": req.to_account_id,
                "payee_handle": req.payee_handle,
                "description": req.description,
                "external_reference": req.external_reference,
                "merchant": req.merchant,
            },
            "customer_id": req.customer_id,
            "initiated_via": req.initiated_via,
            "agent": req.agent.as_ref().map(|a| json!({
                "agent_id": a.agent_id,
                "mandate_id": a.mandate_id,
                "cap_override": a.cap_override,
                "approval_latency_seconds": a.approval_latency_seconds,
            })),
            "session": req.session.as_ref().map(|s| json!({
                "session_id": s.session_id,
                "ip_address": s.ip_address,
                "user_agent": s.user_agent,
                "device_fingerprint": s.device_fingerprint,
                "session_created_at": s.session_created_at,
                "last_activity_at": s.last_activity_at,
            })),
            // Wall clock unless this deployment accepts a supplied instant
            // and the caller sent one (see fraud::gate::screen).
            "requested_at": req.requested_at.unwrap_or_else(chrono::Utc::now),
        })
    }
}

fn parse_action(action: &str) -> FraudAction {
    match action {
        "allow" => FraudAction::Allow,
        "block" => FraudAction::Block,
        "challenge" => FraudAction::Challenge,
        "delay_and_warn" => FraudAction::DelayAndWarn,
        // hold_review and anything the contract grows later: safest treatment
        _ => FraudAction::HoldReview,
    }
}

#[async_trait]
impl FraudCheck for EngineFraudCheck {
    fn backend(&self) -> &'static str {
        "engine"
    }

    async fn assess(&self, req: &FraudRequest) -> Result<FraudDecision, FraudCheckError> {
        if self.decisions_breaker.open() {
            return Err(FraudCheckError::Transport("circuit open".to_string()));
        }
        let sent = self
            .http
            .post(format!("{}/v1/decisions", self.base_url))
            .bearer_auth(&self.token)
            .json(&Self::wire_body(req))
            .send()
            .await;
        let resp = match sent {
            Ok(resp) => resp,
            Err(e) => {
                self.decisions_breaker.record_failure();
                return Err(if e.is_timeout() {
                    FraudCheckError::Timeout
                } else {
                    FraudCheckError::Transport(e.to_string())
                });
            }
        };
        let status = resp.status();
        if status.is_server_error() {
            self.decisions_breaker.record_failure();
            let body = resp.text().await.unwrap_or_default();
            return Err(FraudCheckError::Transport(format!("engine 5xx: {body}")));
        }
        if !status.is_success() {
            // 4xx = bank-side contract bug, not engine outage: don't trip the
            // breaker for it, but surface it distinctly.
            let body = resp.text().await.unwrap_or_default();
            return Err(FraudCheckError::Backend {
                status: status.as_u16(),
                body,
            });
        }
        self.decisions_breaker.record_success();
        let value: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| FraudCheckError::Transport(e.to_string()))?;
        let decision_id = value
            .get("decision_id")
            .and_then(|v| v.as_str())
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| FraudCheckError::Transport("missing decision_id".to_string()))?;
        let action = parse_action(value.get("action").and_then(|v| v.as_str()).unwrap_or(""));
        Ok(FraudDecision {
            decision_id,
            action,
            engine_mode: value
                .get("engine_mode")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string(),
            message_for_customer: value
                .get("message_for_customer")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        })
    }

    async fn rescore(&self, req: FraudRequest, executed: bool) {
        let body = json!({
            "original_request": Self::wire_body(&req),
            "executed": executed,
            "failed_open_at": chrono::Utc::now(),
        });
        // Best-effort by contract: never let this influence a request path.
        let result = self
            .http
            .post(format!("{}/v1/rescore", self.base_url))
            .bearer_auth(&self.token)
            .json(&body)
            .send()
            .await;
        if let Err(e) = result {
            tracing::warn!(operation_id = %req.operation_id, error = %e, "fraud rescore not delivered");
        }
    }

    /// Deliver one denial from the outbox. Unlike `rescore` above, this reports
    /// its outcome honestly rather than swallowing it: the drainer marks the
    /// row delivered only on success, and a row wrongly marked delivered is
    /// lost for good.
    ///
    /// It also does two things `rescore` does not, deliberately: it checks the
    /// response status (a 500 or a 401 is a failure, not a delivery), and it
    /// participates in the circuit breaker, so a dead engine stops a batch of
    /// 100 from each burning a full timeout.
    async fn report_denial(&self, payload: &serde_json::Value) -> Result<(), FraudCheckError> {
        if self.telemetry_breaker.open() {
            return Err(FraudCheckError::Transport("circuit open".to_string()));
        }
        let resp = self
            .telemetry_http
            .post(format!("{}/v1/outcomes", self.base_url))
            .bearer_auth(&self.token)
            .json(payload)
            .send()
            .await;
        let resp = match resp {
            Ok(r) => r,
            Err(e) if e.is_timeout() => {
                self.telemetry_breaker.record_failure();
                return Err(FraudCheckError::Timeout);
            }
            Err(e) => {
                self.telemetry_breaker.record_failure();
                return Err(FraudCheckError::Transport(e.to_string()));
            }
        };
        let status = resp.status();
        if status.is_success() {
            self.telemetry_breaker.record_success();
            return Ok(());
        }
        let body = resp.text().await.unwrap_or_default();
        if status.is_server_error() {
            self.telemetry_breaker.record_failure();
            return Err(FraudCheckError::Transport(format!(
                "engine {status}: {body}"
            )));
        }
        // 4xx is a bank-side contract bug (a malformed payload, a stale token),
        // not an engine outage — same reasoning as `assess`. Do not trip the
        // breaker, but do fail the row so it retries into its dead-letter cap
        // rather than being marked delivered.
        Err(FraudCheckError::Backend {
            status: status.as_u16(),
            body,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(requested_at: Option<chrono::DateTime<chrono::Utc>>) -> FraudRequest {
        FraudRequest {
            operation_id: uuid::Uuid::new_v4(),
            idempotency_key: "k".to_string(),
            kind: "transfer",
            amount: rust_decimal::Decimal::new(150000, 2),
            from_account_id: uuid::Uuid::new_v4(),
            to_account_id: None,
            payee_handle: None,
            description: None,
            external_reference: None,
            merchant: None,
            customer_id: uuid::Uuid::new_v4(),
            initiated_via: "web",
            agent: None,
            session: None,
            requested_at,
        }
    }

    /// The field has been on the wire contract since it was written; until the
    /// engine's #21 it was read nowhere, and until this change the bank always
    /// overwrote it with `now()`. Both halves have to hold for a replayed
    /// corpus to have a time axis at all.
    #[test]
    fn a_supplied_instant_reaches_the_wire() {
        let instant = chrono::DateTime::parse_from_rfc3339("2024-04-01T09:15:00Z")
            .unwrap()
            .with_timezone(&chrono::Utc);

        let body = EngineFraudCheck::wire_body(&request(Some(instant)));

        // Compare instants, not strings: serde's RFC 3339 rendering omits
        // zero subsecond digits, so a string assertion would pin the
        // serialiser's formatting rather than the value that was sent.
        let stamped = chrono::DateTime::parse_from_rfc3339(body["requested_at"].as_str().unwrap())
            .unwrap()
            .with_timezone(&chrono::Utc);
        assert_eq!(stamped, instant);
    }

    /// The default path, which is every real movement: no supplied instant
    /// means the bank stamps its own clock, exactly as before this change.
    #[test]
    fn without_one_the_bank_stamps_its_own_clock() {
        let before = chrono::Utc::now();
        let body = EngineFraudCheck::wire_body(&request(None));
        let after = chrono::Utc::now();

        let stamped = chrono::DateTime::parse_from_rfc3339(body["requested_at"].as_str().unwrap())
            .unwrap()
            .with_timezone(&chrono::Utc);
        assert!(
            before <= stamped && stamped <= after,
            "expected wall clock, got {stamped}"
        );
    }

    /// The two endpoints must not be able to trip each other's breaker.
    ///
    /// The failure this guards against is asymmetric and expensive in one
    /// direction: a struggling `/v1/outcomes` during a 100-row drain is a
    /// background inconvenience, but if it opens the breaker in front of
    /// `/v1/decisions` it starts failing live money movement over telemetry.
    ///
    /// Asserted on breaker state rather than on the error `assess` returns,
    /// because `assess` against an unroutable host fails either way — the
    /// message would be the only difference, and a test that can only tell
    /// "circuit open" from "connection refused" by string is a test that breaks
    /// on a reqwest upgrade rather than on a regression.
    #[tokio::test]
    async fn telemetry_failures_do_not_open_the_decisions_breaker() {
        // Reserved-for-documentation address: nothing listens, and nothing
        // resolves it to somewhere that might.
        let engine = EngineFraudCheck::new("http://192.0.2.1:1", "t", 50, 50);

        for _ in 0..BREAKER_THRESHOLD {
            assert!(
                engine
                    .report_denial(&json!({ "event_key": "x" }))
                    .await
                    .is_err(),
                "the drain must fail against a dead engine"
            );
        }

        assert!(
            engine.telemetry_breaker.open(),
            "{BREAKER_THRESHOLD} consecutive telemetry failures must open its own breaker"
        );
        assert!(
            !engine.decisions_breaker.open(),
            "the request path must be untouched by an outcomes outage"
        );
    }
}

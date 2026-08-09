//! Integration tests for the FraudCheck port.
//!
//! Same harness as `tests/transactions.rs`: every test probes `GET /health`
//! and **skips (still passes)** when the API isn't running.
//!
//! Two tiers:
//! - The baseline test runs in ANY fraud mode (off or engine): money movement
//!   must keep working — the port's first promise is zero behavior change by
//!   default.
//! - The engine-mode tests additionally require the fraud engine live and the
//!   bank started with `NANO_BANK__FRAUD__BACKEND=engine`; they skip unless
//!   `FRAUD_E2E=1` is set (the harness can't introspect the bank's backend).
//!
//! Run the full tier against a live stack:
//! ```bash
//! # engine repo: ./start-engine.sh   bank: NANO_BANK__FRAUD__BACKEND=engine cargo run
//! cd api && FRAUD_E2E=1 cargo test --test fraud_port -- --nocapture
//! ```
//! Overrides: `NANO_BANK_TEST_URL`, `NANO_BANK_TEST_DB_URL`,
//! `FRAUD_ENGINE_TEST_URL` (default http://localhost:8092),
//! `FRAUD_ADMIN_TOKEN` (default dev-admin-token).

use serde_json::{json, Value};
use uuid::Uuid;

const TEST_PASSWORD: &str = "securepass123";

fn base_url() -> String {
    std::env::var("NANO_BANK_TEST_URL").unwrap_or_else(|_| "http://localhost:8081".to_string())
}

fn engine_url() -> String {
    std::env::var("FRAUD_ENGINE_TEST_URL").unwrap_or_else(|_| "http://localhost:8092".to_string())
}

fn admin_token() -> String {
    std::env::var("FRAUD_ADMIN_TOKEN").unwrap_or_else(|_| "dev-admin-token".to_string())
}

fn client() -> reqwest::Client {
    reqwest::Client::new()
}

async fn stack_up(c: &reqwest::Client) -> bool {
    matches!(
        c.get(format!("{}/health", base_url())).send().await,
        Ok(r) if r.status().is_success()
    )
}

async fn engine_up(c: &reqwest::Client) -> bool {
    matches!(
        c.get(format!("{}/health", engine_url())).send().await,
        Ok(r) if r.status().is_success()
    )
}

macro_rules! require_stack {
    ($c:expr) => {
        if !stack_up($c).await {
            eprintln!("SKIP: bank API not reachable");
            return;
        }
    };
}

macro_rules! require_fraud_e2e {
    ($c:expr) => {
        if std::env::var("FRAUD_E2E").as_deref() != Ok("1") {
            eprintln!(
                "SKIP: set FRAUD_E2E=1 (bank must run with NANO_BANK__FRAUD__BACKEND=engine)"
            );
            return;
        }
        if !engine_up($c).await {
            eprintln!("SKIP: fraud engine not reachable");
            return;
        }
    };
}

async fn create_customer(c: &reqwest::Client) -> (Uuid, String) {
    let n = Uuid::new_v4().as_u128();
    let email = format!("fraudtest_{}@example.com", n % 1_000_000_000);
    let body = json!({
        "email": email,
        "phone_number": format!("{:010}", (n % 10_000_000_000u128)),
        "first_name": "Fraud",
        "last_name": "Port",
        "date_of_birth": "1990-01-01",
        "sin": format!("{:09}", n % 1_000_000_000),
        "password": TEST_PASSWORD
    });
    let resp = c
        .post(format!("{}/api/v1/customers", base_url()))
        .json(&body)
        .send()
        .await
        .unwrap();
    assert!(
        resp.status().is_success(),
        "create customer: {}",
        resp.status()
    );
    let v: Value = resp.json().await.unwrap();
    (
        Uuid::parse_str(v["customer_id"].as_str().unwrap()).unwrap(),
        email,
    )
}

/// Login carrying a device fingerprint — the context the fraud engine keys
/// device rules and blocklists on (recovered per-transaction via the session).
async fn login_with_device(c: &reqwest::Client, email: &str, device: &str) -> String {
    let resp = c
        .post(format!("{}/api/v1/auth/login", base_url()))
        .json(&json!({
            "email": email,
            "password": TEST_PASSWORD,
            "device_fingerprint": device
        }))
        .send()
        .await
        .unwrap();
    assert!(resp.status().is_success(), "login: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    v["access_token"].as_str().unwrap().to_string()
}

async fn create_account(c: &reqwest::Client, token: &str) -> Uuid {
    let resp = c
        .post(format!("{}/api/v1/accounts", base_url()))
        .bearer_auth(token)
        .json(&json!({ "account_type": "chequing" }))
        .send()
        .await
        .unwrap();
    assert!(
        resp.status().is_success(),
        "create account: {}",
        resp.status()
    );
    let v: Value = resp.json().await.unwrap();
    Uuid::parse_str(v["account_id"].as_str().unwrap()).unwrap()
}

/// Deposit; skips (None) when the GL core is down — same convention as
/// `tests/transactions.rs::seed_deposit`.
async fn seed_deposit(c: &reqwest::Client, token: &str, account: Uuid, amount: f64) -> Option<()> {
    let resp = c
        .post(format!("{}/api/v1/transactions/deposit", base_url()))
        .bearer_auth(token)
        .json(&json!({ "account_id": account, "amount": amount, "description": "seed" }))
        .send()
        .await
        .unwrap();
    if resp.status().as_u16() == 503 {
        eprintln!("SKIP: GL core unavailable (deposit returned 503)");
        return None;
    }
    assert!(resp.status().is_success(), "deposit: {}", resp.status());
    Some(())
}

async fn transfer(
    c: &reqwest::Client,
    token: &str,
    from: Uuid,
    to: Uuid,
    amount: f64,
) -> reqwest::Response {
    c.post(format!("{}/api/v1/transactions/transfer", base_url()))
        .bearer_auth(token)
        .json(&json!({
            "from_account_id": from,
            "to_account_id": to,
            "amount": amount,
            "description": "fraud port test"
        }))
        .send()
        .await
        .unwrap()
}

async fn register_agent(c: &reqwest::Client) -> (Uuid, String) {
    let v: Value = c
        .post(format!("{}/api/v1/agents", base_url()))
        .json(&json!({"display_name": "Fraud Port Agent", "description": "fraud_port e2e"}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    (
        Uuid::parse_str(v["agent_id"].as_str().unwrap()).unwrap(),
        v["agent_secret"].as_str().unwrap().to_string(),
    )
}

async fn mandated_agent_token(c: &reqwest::Client, token: &str, account: Uuid) -> (Uuid, String) {
    let (agent_id, secret) = register_agent(c).await;
    let granted: Value = c
        .post(format!("{}/api/v1/mandates", base_url()))
        .bearer_auth(token)
        .json(&json!({
            "agent_id": agent_id,
            "account_id": account,
            "scopes": ["transfer:initiate"],
            // Caps are mandatory with transfer:initiate; both sit above the test
            // amount so the refusal comes from the engine, not the step-up path.
            "max_per_tx": 100,
            "daily_cap": 500,
            "expires_at": (chrono::Utc::now() + chrono::Duration::hours(1)).to_rfc3339(),
        }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let mandate = Uuid::parse_str(granted["mandate_id"].as_str().unwrap()).unwrap();
    let issued: Value = c
        .post(format!("{}/api/v1/auth/agent-token", base_url()))
        .json(&json!({"agent_id": agent_id, "agent_secret": secret, "mandate_id": mandate}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    (
        mandate,
        issued["access_token"].as_str().unwrap().to_string(),
    )
}

async fn agent_transfer(
    c: &reqwest::Client,
    atoken: &str,
    to: Uuid,
    amount: f64,
) -> reqwest::Response {
    c.post(format!("{}/api/v1/agent/transfers", base_url()))
        .bearer_auth(atoken)
        .json(&json!({
            "to_account_id": to,
            "amount": amount,
            "description": "agent payment",
            "idempotency_key": Uuid::new_v4().to_string(),
        }))
        .send()
        .await
        .unwrap()
}

async fn engine_list_add(c: &reqwest::Client, list: &str, key: &str) -> Value {
    let created = c
        .post(format!("{}/admin/v1/lists", engine_url()))
        .bearer_auth(admin_token())
        .header("X-Actor", "fraud-port-e2e")
        .json(&json!({"list_name": list, "entry_key": key, "reason": "fraud_port e2e test"}))
        .send()
        .await
        .unwrap();
    assert_eq!(created.status().as_u16(), 201, "engine list add");
    created.json().await.unwrap()
}

async fn engine_list_revoke(c: &reqwest::Client, entry: &Value) {
    let revoked = c
        .delete(format!(
            "{}/admin/v1/lists/{}",
            engine_url(),
            entry["entry_id"].as_str().unwrap()
        ))
        .bearer_auth(admin_token())
        .header("X-Actor", "fraud-port-e2e")
        .send()
        .await
        .unwrap();
    assert_eq!(revoked.status().as_u16(), 204, "engine list revoke");
}

/// The engine's own database, for asserting what it did — or did not — record.
/// `None` with a SKIP note when unreachable, same convention as `test_db`.
async fn engine_db() -> Option<sqlx::PgPool> {
    let url = std::env::var("FRAUD_ENGINE_TEST_DB_URL")
        .unwrap_or_else(|_| "postgres://fraud:fraud@localhost:5436/fraud_engine".to_string());
    match sqlx::PgPool::connect(&url).await {
        Ok(pool) => Some(pool),
        Err(e) => {
            eprintln!("SKIP engine DB assertions: {e}");
            None
        }
    }
}

async fn test_db() -> Option<sqlx::PgPool> {
    let url = std::env::var("NANO_BANK_TEST_DB_URL").unwrap_or_else(|_| {
        "postgres://nanobank_user:secure_nano_password_2024!@[::1]:5432/nano_bank_db".to_string()
    });
    match sqlx::PgPool::connect(&url).await {
        Ok(pool) => Some(pool),
        Err(e) => {
            eprintln!("SKIP DB assertions: {e}");
            None
        }
    }
}

/// Tier 1 — any mode: the port's default must not change bank behavior.
#[tokio::test]
async fn transfers_still_work_with_port_in_place() {
    let c = client();
    require_stack!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, "fraud-port-baseline-device").await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }
    let resp = transfer(&c, &token, from, to, 50.0).await;
    assert!(resp.status().is_success(), "transfer: {}", resp.status());
}

/// Tier 2 — engine mode: an allowed transfer carries the engine linkage in
/// `transactions.metadata.fraud` (decision_id proves the round trip).
#[tokio::test]
async fn engine_mode_stamps_decision_linkage() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }
    let resp = transfer(&c, &token, from, to, 40.0).await;
    assert!(resp.status().is_success(), "transfer: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    let txn_id = Uuid::parse_str(v["transaction_id"].as_str().unwrap()).unwrap();

    let Some(pool) = test_db().await else { return };
    let (op_id, decision_id): (Option<String>, Option<String>) = sqlx::query_as(
        "SELECT metadata->'fraud'->>'operation_id', metadata->'fraud'->>'decision_id' \
         FROM transactions WHERE transaction_id = $1",
    )
    .bind(txn_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(op_id.is_some(), "fraud.operation_id stamped");
    assert!(
        decision_id.is_some(),
        "fraud.decision_id stamped (engine round trip)"
    );
}

/// Tier 2 — engine mode: an engine refusal on the AGENT plane leaves exactly one
/// audit row, carrying the risk reason.
///
/// Regression guard. The gate used to audit the decline itself while the agent
/// handler's catch-all audited every failure too, so the owner's activity view
/// showed the real `RISK_REVIEW` beside a contradictory `denied / INTERNAL` — the
/// catch-all had no arm for the fraud errors. One writer, one row, right reason.
#[tokio::test]
async fn engine_mode_agent_refusal_audits_once() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, &format!("agent-dev-{}", Uuid::new_v4())).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;

    // The engine watches the destination, so any payment to it is held. No funding
    // needed: screening happens before the money transaction opens.
    let entry = engine_list_add(&c, "account_watch", &to.to_string()).await;
    let (mandate, atoken) = mandated_agent_token(&c, &token, from).await;

    let resp = agent_transfer(&c, &atoken, to, 40.0).await;
    assert_eq!(resp.status().as_u16(), 403, "watched destination must 403");
    let v: Value = resp.json().await.unwrap();
    // The agent plane returns the OPAQUE refusal, not the specific review code:
    // `refusal_for_agent` (handlers/agent_api.rs) collapses every refusal —
    // including hold_review — to `TRANSFER_REFUSED`, because *why* a transfer was
    // refused is deliberately not the agent's business. The specific reason
    // survives for the granting customer in `agent_actions` (asserted below), not
    // in the HTTP body. Do not "fix" this back to TRANSACTION_UNDER_REVIEW.
    assert_eq!(v["error"]["code"], "TRANSFER_REFUSED");

    if let Some(db) = test_db().await {
        let rows: Vec<(String, Option<String>)> = sqlx::query_as(
            "SELECT decision, reason FROM agent_actions \
             WHERE mandate_id = $1 AND operation = 'transfer' ORDER BY created_at",
        )
        .bind(mandate)
        .fetch_all(&db)
        .await
        .unwrap();
        assert_eq!(
            rows,
            vec![("denied".to_string(), Some("RISK_REVIEW".to_string()))],
            "exactly one audit row, with the risk reason"
        );
    }
    engine_list_revoke(&c, &entry).await;
}

/// Tier 2 — engine mode: a device the fraud engine blocklists makes the bank
/// refuse the movement with the opaque decline, before any money moves.
#[tokio::test]
async fn engine_mode_blocked_device_declines() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let device = format!("blocked-dev-{}", Uuid::new_v4());
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, &device).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }

    // Analyst blocks the device on the engine side...
    let created = c
        .post(format!("{}/admin/v1/lists", engine_url()))
        .bearer_auth(admin_token())
        .header("X-Actor", "fraud-port-e2e")
        .json(&json!({
            "list_name": "device_block",
            "entry_key": device,
            "reason": "fraud_port e2e test"
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(created.status().as_u16(), 201, "engine blocklist add");
    let entry: Value = created.json().await.unwrap();

    // ...and the bank now refuses this session's transfers, opaquely.
    let resp = transfer(&c, &token, from, to, 40.0).await;
    assert_eq!(resp.status().as_u16(), 403, "blocked device must 403");
    let v: Value = resp.json().await.unwrap();
    assert_eq!(v["error"]["code"], "TRANSACTION_DECLINED");

    // Cleanup: revoke so repeated runs stay independent.
    let revoked = c
        .delete(format!(
            "{}/admin/v1/lists/{}",
            engine_url(),
            entry["entry_id"].as_str().unwrap()
        ))
        .bearer_auth(admin_token())
        .header("X-Actor", "fraud-port-e2e")
        .send()
        .await
        .unwrap();
    assert_eq!(revoked.status().as_u16(), 204, "engine blocklist revoke");
}

/// Tier 2 — engine mode: a retried rail movement must NOT reach the engine again.
///
/// The four rail handlers used to screen *before* their idempotency replay, so a
/// bank retry of an already-posted AFT/Interac/Lynx movement re-invoked the
/// engine: velocity counted twice, a second decision row per retry, and above
/// `fail_closed_above` a 503 for a request that had already succeeded
/// (`design/INTEGRATION_DESIGN.md` §5 requires the replay to short-circuit
/// first). The ordering is invisible on inspection — two adjacent blocks — so it
/// gets a test rather than a comment.
///
/// Uses the AFT credit rail deliberately: originating accrues into the open batch
/// and moves no money until settlement, so the assertion needs no funded account
/// and runs where the funded-flow tests skip.
#[tokio::test]
async fn engine_mode_retried_rail_send_is_not_rescreened() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, &format!("rail-dev-{}", Uuid::new_v4())).await;
    let from = create_account(&c, &token).await;

    // A FRESH counterparty per run, not a fixed one: the engine tracks payee-side
    // velocity, so a hardcoded account accumulates inbound attempts across runs
    // until `payee_inbound_24h_high` joins the novelty codes and the engine starts
    // holding the originate — a self-poisoning test.
    let counterparty_account = format!("{:07}", Uuid::new_v4().as_u128() % 10_000_000);
    let key = format!("rail-idem-{}", Uuid::new_v4());
    let body = json!({
        "originator_account_id": from,
        "counterparty_institution": "003",
        "counterparty_transit": "12345",
        "counterparty_account": counterparty_account,
        "payee_name": "Utility Co",
        "amount": 40.0,
        "idempotency_key": key,
    });
    let originate = |b: serde_json::Value| {
        let c = c.clone();
        let token = token.clone();
        async move {
            c.post(format!("{}/api/v1/aft/credits", base_url()))
                .bearer_auth(&token)
                .json(&b)
                .send()
                .await
                .unwrap()
        }
    };
    let first = originate(body.clone()).await;
    assert_eq!(first.status().as_u16(), 201, "first originate");
    let first_id = first.json::<Value>().await.unwrap()["entry_id"].clone();

    let replay = originate(body).await;
    assert_eq!(replay.status().as_u16(), 201, "replay returns the original");
    assert_eq!(
        replay.json::<Value>().await.unwrap()["entry_id"],
        first_id,
        "replay must be the same entry, not a second one"
    );

    // Counting decision ROWS cannot detect this: the engine is idempotent on the
    // same key, so a re-screened retry replays the stored decision instead of
    // inserting another. What it cannot undo is the velocity it already counted —
    // the engine records every assessed attempt before it notices the replay.
    //
    // So drive one further originate under a fresh key and read the velocity the
    // engine saw for this customer. Exactly one prior attempt means the retry
    // never reached it; two means it did.
    let probe_key = format!("rail-probe-{}", Uuid::new_v4());
    let probe = originate(json!({
        "originator_account_id": from,
        "counterparty_institution": "003",
        "counterparty_transit": "12345",
        "counterparty_account": counterparty_account,
        "payee_name": "Utility Co",
        "amount": 41.0,
        "idempotency_key": probe_key,
    }))
    .await;
    assert_eq!(probe.status().as_u16(), 201, "probe originate");

    if let Some(db) = engine_db().await {
        let vector: Value = sqlx::query_scalar(
            "SELECT feature_vector FROM decisions WHERE idempotency_key LIKE $1",
        )
        .bind(format!("%{probe_key}"))
        .fetch_one(&db)
        .await
        .unwrap();
        let counts: Vec<(String, i64)> = vector
            .as_object()
            .expect("feature vector object")
            .iter()
            .filter(|(k, _)| k.starts_with("velocity:customer_id:"))
            .map(|(k, v)| (k.clone(), v["count"].as_i64().unwrap_or(-1)))
            .collect();
        assert!(!counts.is_empty(), "no customer velocity in {vector}");
        for (key, count) in &counts {
            assert_eq!(
                *count, 1,
                "the engine should have seen ONE prior originate, not {count} ({key}) — \
                 a retry was re-screened and double-counted velocity"
            );
        }
    }
}

/// Mint a service-plane token — the fraud operator's identity.
async fn service_token(c: &reqwest::Client) -> String {
    let r = c
        .post(format!("{}/api/v1/auth/service-token", base_url()))
        .json(&json!({ "client_secret": "nano-bank-visa-network-secret-change-me" }))
        .send()
        .await
        .unwrap();
    assert!(r.status().is_success(), "service-token: {}", r.status());
    let v: Value = r.json().await.unwrap();
    v["access_token"].as_str().unwrap().to_string()
}

async fn fraud_link(c: &reqwest::Client, token: &str, txn: Uuid) -> reqwest::Response {
    c.get(format!(
        "{}/api/v1/fraud/admin/transactions/{txn}/fraud-link",
        base_url()
    ))
    .bearer_auth(token)
    .send()
    .await
    .unwrap()
}

/// Tier 2 — engine mode: the linkage endpoint hands the engine's `operation_id`
/// to a service caller (#46).
///
/// This is the key the whole label path turns on. The engine joins ground truth
/// on `outcome_events.operation_id = decisions.operation_id` and has no
/// `transaction_id` column to fall back on, so until this endpoint existed the
/// id was written to `transactions.metadata` and read by nobody — no decision
/// could be labelled, and the training-set export returned zero rows.
///
/// Asserted against the database rather than merely "is a UUID": the endpoint
/// returning *some* well-formed id that isn't the one the engine recorded would
/// be worse than returning nothing, because every downstream label would attach
/// to the wrong decision.
#[tokio::test]
async fn fraud_link_exposes_the_engine_operation_id_to_a_service_caller() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }
    let resp = transfer(&c, &token, from, to, 40.0).await;
    assert!(resp.status().is_success(), "transfer: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    let txn_id = Uuid::parse_str(v["transaction_id"].as_str().unwrap()).unwrap();

    // The customer plane must not carry it — that is the disclosure decision
    // this endpoint exists to honour (#46: service plane only).
    assert!(
        v.get("metadata").is_none_or(Value::is_null),
        "engine ids must not reach the customer plane: {v}"
    );

    let svc = service_token(&c).await;
    let link = fraud_link(&c, &svc, txn_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let link: Value = link.json().await.unwrap();

    let Some(pool) = test_db().await else { return };
    let (op_id, decision_id): (Option<String>, Option<String>) = sqlx::query_as(
        "SELECT metadata->'fraud'->>'operation_id', metadata->'fraud'->>'decision_id' \
         FROM transactions WHERE transaction_id = $1",
    )
    .bind(txn_id)
    .fetch_one(&pool)
    .await
    .unwrap();

    assert_eq!(
        link["operation_id"].as_str(),
        op_id.as_deref(),
        "must return the id the engine actually recorded: {link}"
    );
    assert_eq!(link["decision_id"].as_str(), decision_id.as_deref());
    assert_eq!(link["transaction_id"].as_str().unwrap(), txn_id.to_string());
}

/// The linkage is service-plane only: a customer token is refused even for the
/// customer's own transaction. Without this the endpoint would be a way to read
/// engine internals from the customer plane — the thing #46 chose against.
#[tokio::test]
async fn fraud_link_refuses_a_customer_token() {
    let c = client();
    require_stack!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }
    let resp = transfer(&c, &token, from, to, 25.0).await;
    assert!(resp.status().is_success());
    let v: Value = resp.json().await.unwrap();
    let txn_id = Uuid::parse_str(v["transaction_id"].as_str().unwrap()).unwrap();

    let status = fraud_link(&c, &token, txn_id).await.status().as_u16();
    assert_eq!(status, 403, "a customer token is the wrong plane");
}

/// An unscreened transaction answers 200 with nulls, not 404.
///
/// "This transaction has no fraud link" is a true answer — screening is off by
/// default, and several rails do not screen at all. A 404 would tell a caller
/// the transaction does not exist and invite it to retry something that is never
/// going to appear.
///
/// Runs WITHOUT `require_fraud_e2e`: it needs the backend off, which is the
/// shipped default, so this is the one linkage test that exercises the
/// unscreened branch.
#[tokio::test]
async fn fraud_link_is_null_for_an_unscreened_transaction() {
    let c = client();
    require_stack!(&c);
    if std::env::var("FRAUD_E2E").as_deref() == Ok("1") {
        eprintln!("SKIP: needs the fraud backend off (the default)");
        return;
    }
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let from = create_account(&c, &token).await;
    let to = create_account(&c, &token).await;
    if seed_deposit(&c, &token, from, 500.0).await.is_none() {
        return;
    }
    let resp = transfer(&c, &token, from, to, 30.0).await;
    assert!(resp.status().is_success());
    let v: Value = resp.json().await.unwrap();
    let txn_id = Uuid::parse_str(v["transaction_id"].as_str().unwrap()).unwrap();

    let svc = service_token(&c).await;
    let link = fraud_link(&c, &svc, txn_id).await;
    assert_eq!(link.status().as_u16(), 200, "unscreened is not 'not found'");
    let link: Value = link.json().await.unwrap();
    assert!(link["operation_id"].is_null(), "{link}");
    assert!(link["decision_id"].is_null(), "{link}");
    assert_eq!(link["failed_open"], false);
}

/// An unknown transaction is a 404 — the one case that IS "not found".
#[tokio::test]
async fn fraud_link_404s_for_an_unknown_transaction() {
    let c = client();
    require_stack!(&c);
    let svc = service_token(&c).await;
    let status = fraud_link(&c, &svc, Uuid::new_v4()).await.status().as_u16();
    assert_eq!(status, 404);
}

/// A screened **rail** movement resolves to the engine's decision (#52).
///
/// This test used to pin the opposite. Interac, AFT and Lynx create real
/// `transactions` rows and call `screen()`, but never stamped
/// `metadata.fraud` — so their decisions, possibly **blocks**, were unreachable
/// and `fraud-link` answered nulls indistinguishable from "never screened".
/// That is what made a null uninterpretable, and it is now fixed for the rails
/// whose screening and money movement share a request.
///
/// Asserted against the engine's own `decisions` row, not merely "is a UUID":
/// a well-formed id that points at the wrong decision is worse than a null,
/// because every label downstream attaches silently to the wrong thing.
#[tokio::test]
async fn fraud_link_resolves_a_screened_rail_movement() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let account = create_account(&c, &token).await;
    if seed_deposit(&c, &token, account, 5000.0).await.is_none() {
        return;
    }

    let sent = c
        .post(format!("{}/api/v1/interac/etransfers", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "from_account_id": account,
            "amount": 60.00,
            "recipient_handle_type": "email",
            "recipient_handle_value": format!("rail-{}@example.com", Uuid::new_v4()),
            "security_question": "q",
            "security_answer": "a",
            "idempotency_key": format!("rail-{}", Uuid::new_v4()),
        }))
        .send()
        .await
        .unwrap();
    assert!(sent.status().is_success(), "interac send: {}", sent.status());

    // The rail hands back an `etransfer_id`; the row that was screened is the
    // `interac_hold` it wrote through `new_txn`.
    let Some(pool) = test_db().await else { return };
    let (txn_id,): (Uuid,) = sqlx::query_as(
        "SELECT transaction_id FROM transactions \
         WHERE transaction_type LIKE 'interac\\_%' ORDER BY created_at DESC LIMIT 1",
    )
    .fetch_one(&pool)
    .await
    .unwrap();

    let svc = service_token(&c).await;
    let link = fraud_link(&c, &svc, txn_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let link: Value = link.json().await.unwrap();
    let op_id = link["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("a screened rail movement must resolve: {link}"));

    // It has to be the decision the ENGINE recorded, not just a well-formed id.
    let Some(engine) = engine_db().await else { return };
    let seen: i64 = sqlx::query_scalar("SELECT count(*) FROM decisions WHERE operation_id = $1")
        .bind(Uuid::parse_str(op_id).unwrap())
        .fetch_one(&engine)
        .await
        .unwrap();
    assert_eq!(seen, 1, "operation_id {op_id} must name a real engine decision");
}

/// A settled **AFT** movement resolves to the decision made at origination (#54).
///
/// AFT is the last of the split-request rails: `create_credit` screens and
/// writes only an `aft_entries` row — no `transactions` row exists until the
/// batch settles, which is a separate service-plane request where the
/// `FraudLink` is long gone. So the engine's ruling on a direct deposit,
/// possibly a **block**, was dropped, and `fraud-link` answered nulls
/// indistinguishable from "never screened".
///
/// Two assertions, and the second is the one a naive fix gets wrong: the id must
/// name a real engine decision, **and** be the one minted at origination.
/// Settlement does not re-screen, so a different id here would mean something
/// screened silently.
#[tokio::test]
async fn fraud_link_resolves_a_settled_aft_movement() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let account = create_account(&c, &token).await;
    if seed_deposit(&c, &token, account, 5000.0).await.is_none() {
        return;
    }

    let credit = c
        .post(format!("{}/api/v1/aft/credits", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "originator_account_id": account,
            "amount": 250.00,
            "counterparty_institution": "003",
            "counterparty_transit": "12345",
            "counterparty_account": "9876543",
            "payee_name": "Linkage Payroll",
            "idempotency_key": format!("aft-{}", Uuid::new_v4()),
        }))
        .send()
        .await
        .unwrap();
    assert!(credit.status().is_success(), "aft credit: {}", credit.status());
    let cv: Value = credit.json().await.unwrap();
    let entry_id = Uuid::parse_str(cv["entry_id"].as_str().expect("entry_id")).unwrap();

    // The decision exists now, parked on the entry — settlement only executes it.
    let Some(pool) = test_db().await else { return };
    let (stored,): (Option<Value>,) =
        sqlx::query_as("SELECT metadata FROM aft_entries WHERE entry_id = $1")
            .bind(entry_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let originated_op = stored
        .as_ref()
        .and_then(|m| m.get("fraud"))
        .and_then(|f| f.get("operation_id"))
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("origination must record its linkage: {stored:?}"))
        .to_string();

    // Submit and settle the batch the entry landed in.
    let (batch_id,): (Uuid,) =
        sqlx::query_as("SELECT batch_id FROM aft_entries WHERE entry_id = $1")
            .bind(entry_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let svc = service_token(&c).await;
    let submit = c
        .post(format!("{}/api/v1/aft/batches/{batch_id}/submit", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert!(submit.status().is_success(), "submit: {}", submit.status());
    let settle = c
        .post(format!("{}/api/v1/aft/network/settle/{batch_id}", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert!(settle.status().is_success(), "settle: {}", settle.status());

    let (settle_txn,): (Option<Uuid>,) =
        sqlx::query_as("SELECT settle_transaction_id FROM aft_entries WHERE entry_id = $1")
            .bind(entry_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let settle_txn = settle_txn.expect("settlement must record its transaction");

    let link = fraud_link(&c, &svc, settle_txn).await;
    assert_eq!(link.status().as_u16(), 200);
    let link: Value = link.json().await.unwrap();
    let op_id = link["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("a settled AFT movement must resolve: {link}"));

    assert_eq!(
        op_id, originated_op,
        "settlement must carry the origination decision, not a new screening"
    );

    let Some(engine) = engine_db().await else { return };
    let seen: i64 = sqlx::query_scalar("SELECT count(*) FROM decisions WHERE operation_id = $1")
        .bind(Uuid::parse_str(op_id).unwrap())
        .fetch_one(&engine)
        .await
        .unwrap();
    assert_eq!(seen, 1, "operation_id {op_id} must name a real engine decision");
}

/// A captured **card purchase** resolves to the decision made at authorize (#54).
///
/// Cards are the awkward case: `screen()` runs in `authorize`, but the
/// `transactions` row is not written until `capture` — a separate request where
/// the `FraudLink` no longer exists. Before this the engine's ruling on a card
/// purchase, possibly a **block**, was simply dropped, and `fraud-link` answered
/// nulls indistinguishable from "never screened".
///
/// Two things are asserted, and the second is the one a naive fix gets wrong:
/// the id must name a real engine decision, **and** it must be the one minted at
/// authorize. Capture does not re-screen — it settles a decision already made —
/// so a *different* id appearing here would mean something screened silently.
#[tokio::test]
async fn fraud_link_resolves_a_captured_card_purchase() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let card = c
        .post(format!("{}/api/v1/accounts", base_url()))
        .bearer_auth(&token)
        .json(&json!({ "account_type": "credit_card" }))
        .send()
        .await
        .unwrap();
    assert!(card.status().is_success(), "create card: {}", card.status());
    let cv: Value = card.json().await.unwrap();
    let card_id = Uuid::parse_str(cv["account_id"].as_str().unwrap()).unwrap();

    let svc = service_token(&c).await;
    let auth = c
        .post(format!("{}/api/v1/cards/authorize", base_url()))
        .bearer_auth(&svc)
        .json(&json!({ "account_id": card_id, "amount": 42.50, "merchant": "Linkage Co" }))
        .send()
        .await
        .unwrap();
    assert!(auth.status().is_success(), "authorize: {}", auth.status());
    let av: Value = auth.json().await.unwrap();
    assert_eq!(av["status"], "approved", "authorize should approve: {av}");
    let auth_id = av["auth_id"].as_str().expect("auth_id").to_string();

    // The decision was made above; capture only settles it.
    let Some(pool) = test_db().await else { return };
    let (held,): (Option<Value>,) =
        sqlx::query_as("SELECT metadata FROM account_holds WHERE hold_id = $1")
            .bind(Uuid::parse_str(&auth_id).unwrap())
            .fetch_one(&pool)
            .await
            .unwrap();
    let authorized_op = held
        .as_ref()
        .and_then(|m| m.get("fraud"))
        .and_then(|f| f.get("operation_id"))
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("authorize must record its linkage on the hold: {held:?}"))
        .to_string();

    let cap = c
        .post(format!("{}/api/v1/cards/capture", base_url()))
        .bearer_auth(&svc)
        .json(&json!({ "auth_id": auth_id }))
        .send()
        .await
        .unwrap();
    assert!(cap.status().is_success(), "capture: {}", cap.status());
    let cvj: Value = cap.json().await.unwrap();
    let txn_id = Uuid::parse_str(
        cvj["transaction_id"]
            .as_str()
            .unwrap_or_else(|| panic!("capture must return a transaction_id: {cvj}")),
    )
    .unwrap();

    let link = fraud_link(&c, &svc, txn_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let link: Value = link.json().await.unwrap();
    let op_id = link["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("a captured card purchase must resolve: {link}"));

    assert_eq!(
        op_id, authorized_op,
        "the purchase must carry the decision from authorize, not a new screening"
    );

    let Some(engine) = engine_db().await else { return };
    let seen: i64 = sqlx::query_scalar("SELECT count(*) FROM decisions WHERE operation_id = $1")
        .bind(Uuid::parse_str(op_id).unwrap())
        .fetch_one(&engine)
        .await
        .unwrap();
    assert_eq!(seen, 1, "operation_id {op_id} must name a real engine decision");
}

// ---------------------------------------------------------------------------
// Rail fraud-link resolver: /admin/rails/{rail}/{rail_id}/fraud-link
// ---------------------------------------------------------------------------

async fn fraud_link_rail(
    c: &reqwest::Client,
    token: &str,
    rail: &str,
    rail_id: Uuid,
) -> reqwest::Response {
    c.get(format!(
        "{}/api/v1/fraud/admin/rails/{rail}/{rail_id}/fraud-link",
        base_url()
    ))
    .bearer_auth(token)
    .send()
    .await
    .unwrap()
}

/// The resolver maps a rail's own id to the screened decision — same answer the
/// transaction route gives, without the caller having to know the money
/// `transaction_id`. Asserted against the engine's `decisions` table, not merely
/// "is a UUID": a well-formed id that isn't the one the engine recorded is worse
/// than a null.
#[tokio::test]
async fn rail_fraud_link_resolves_an_interac_etransfer() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let account = create_account(&c, &token).await;
    if seed_deposit(&c, &token, account, 5000.0).await.is_none() {
        return;
    }
    let sent = c
        .post(format!("{}/api/v1/interac/etransfers", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "from_account_id": account,
            "amount": 60.00,
            "recipient_handle_type": "email",
            "recipient_handle_value": format!("rail-{}@example.com", Uuid::new_v4()),
            "security_question": "q",
            "security_answer": "a",
            "idempotency_key": format!("rail-{}", Uuid::new_v4()),
        }))
        .send()
        .await
        .unwrap();
    assert!(sent.status().is_success(), "interac send: {}", sent.status());
    let etransfer_id =
        Uuid::parse_str(sent.json::<Value>().await.unwrap()["etransfer_id"].as_str().unwrap())
            .unwrap();

    let svc = service_token(&c).await;
    let link = fraud_link_rail(&c, &svc, "interac", etransfer_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let op_id = link.json::<Value>().await.unwrap()["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("interac etransfer must resolve"))
        .to_string();
    assert_engine_decision_exists(&c, &op_id).await;
}

/// Lynx: the linkage sits on the send-time hold (`settlement_transaction_id`),
/// stamped at `/wires`; reachable straight away, no network settle needed.
#[tokio::test]
async fn rail_fraud_link_resolves_a_lynx_wire() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let account = create_account(&c, &token).await;
    if seed_deposit(&c, &token, account, 50000.0).await.is_none() {
        return;
    }
    let sent = c
        .post(format!("{}/api/v1/lynx/wires", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "from_account_id": account,
            "amount": 15000.00,
            "counterparty_name": "Acme Corp",
            "counterparty_institution": "003",
            "counterparty_account": "9876543",
            "idempotency_key": format!("rail-{}", Uuid::new_v4()),
        }))
        .send()
        .await
        .unwrap();
    assert!(sent.status().is_success(), "lynx wire: {}", sent.status());
    let sv: Value = sent.json().await.unwrap();
    let wire_id = Uuid::parse_str(sv["wire_id"].as_str().unwrap()).unwrap();

    let svc = service_token(&c).await;
    let link = fraud_link_rail(&c, &svc, "lynx", wire_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let op_id = link.json::<Value>().await.unwrap()["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("lynx wire must resolve"))
        .to_string();
    assert_engine_decision_exists(&c, &op_id).await;
}

/// AFT: keyed on the **entry** id (a batch has many); the linkage lands on
/// `settle_transaction_id`, so the batch is submitted + settled first.
#[tokio::test]
async fn rail_fraud_link_resolves_an_aft_entry() {
    let c = client();
    require_stack!(&c);
    require_fraud_e2e!(&c);
    let (_, email) = create_customer(&c).await;
    let token = login_with_device(&c, &email, format!("dev-{}", Uuid::new_v4()).as_str()).await;
    let account = create_account(&c, &token).await;
    if seed_deposit(&c, &token, account, 5000.0).await.is_none() {
        return;
    }
    let credit = c
        .post(format!("{}/api/v1/aft/credits", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "originator_account_id": account,
            "amount": 250.00,
            "counterparty_institution": "003",
            "counterparty_transit": "12345",
            "counterparty_account": "9876543",
            "payee_name": "Rail Payroll",
            "idempotency_key": format!("rail-{}", Uuid::new_v4()),
        }))
        .send()
        .await
        .unwrap();
    let credit_status = credit.status();
    assert!(credit_status.is_success(), "aft credit: {credit_status}");
    let cv: Value = credit.json().await.unwrap();
    let entry_id = Uuid::parse_str(cv["entry_id"].as_str().unwrap()).unwrap();
    let batch_id = Uuid::parse_str(cv["batch_id"].as_str().unwrap()).unwrap();

    let svc = service_token(&c).await;
    for path in [
        format!("aft/batches/{batch_id}/submit"),
        format!("aft/network/settle/{batch_id}"),
    ] {
        let r = c
            .post(format!("{}/api/v1/{path}", base_url()))
            .bearer_auth(&svc)
            .send()
            .await
            .unwrap();
        assert!(r.status().is_success(), "{path}: {}", r.status());
    }

    let link = fraud_link_rail(&c, &svc, "aft", entry_id).await;
    assert_eq!(link.status().as_u16(), 200);
    let op_id = link.json::<Value>().await.unwrap()["operation_id"]
        .as_str()
        .unwrap_or_else(|| panic!("settled aft entry must resolve"))
        .to_string();
    assert_engine_decision_exists(&c, &op_id).await;
}

/// An unknown rail name is a client error, distinct from an unknown id.
#[tokio::test]
async fn rail_fraud_link_400_for_an_unknown_rail() {
    let c = client();
    require_stack!(&c);
    let svc = service_token(&c).await;
    let r = fraud_link_rail(&c, &svc, "cards", Uuid::new_v4()).await;
    assert_eq!(r.status().as_u16(), 400, "unknown rail is a 400");
}

/// An unknown rail id (or one whose money row isn't written yet) is a 404.
#[tokio::test]
async fn rail_fraud_link_404_for_an_unknown_id() {
    let c = client();
    require_stack!(&c);
    let svc = service_token(&c).await;
    let r = fraud_link_rail(&c, &svc, "interac", Uuid::new_v4()).await;
    assert_eq!(r.status().as_u16(), 404, "unknown rail id is a 404");
}

/// Shared: the resolved operation_id must name a real row in the engine's
/// `decisions` table — a valid-but-wrong id is worse than a null.
async fn assert_engine_decision_exists(_c: &reqwest::Client, op_id: &str) {
    let Some(engine) = engine_db().await else {
        return;
    };
    let seen: i64 = sqlx::query_scalar("SELECT count(*) FROM decisions WHERE operation_id = $1")
        .bind(Uuid::parse_str(op_id).unwrap())
        .fetch_one(&engine)
        .await
        .unwrap();
    assert_eq!(seen, 1, "operation_id {op_id} must name a real engine decision");
}

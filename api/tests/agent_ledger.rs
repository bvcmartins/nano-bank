//! Integration test for the CTO audit endpoint. Graceful-skip harness (mirrors
//! tests/back_office_ops.rs): probes GET /health and returns early (passing)
//! when the API is unreachable, so `cargo test` passes with nothing running.
//! Live: cd api && cargo test --test agent_ledger -- --nocapture
use serde_json::{json, Value};

const SERVICE_SECRET: &str = "nano-bank-visa-network-secret-change-me";

fn base_url() -> String {
    std::env::var("NANO_BANK_TEST_URL").unwrap_or_else(|_| "http://localhost:8081".to_string())
}
fn client() -> reqwest::Client {
    reqwest::Client::new()
}
async fn stack_up(c: &reqwest::Client) -> bool {
    c.get(format!("{}/health", base_url())).send().await.is_ok()
}
async fn service_token(c: &reqwest::Client) -> String {
    let r = c
        .post(format!("{}/api/v1/auth/service-token", base_url()))
        .json(&json!({ "client_secret": SERVICE_SECRET }))
        .send()
        .await
        .unwrap();
    r.json::<Value>().await.unwrap()["access_token"]
        .as_str()
        .unwrap()
        .to_string()
}

#[tokio::test]
async fn records_a_cto_action_and_returns_seq_and_hash() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("skip: stack down");
        return;
    }
    let token = service_token(&c).await;
    let r = c
        .post(format!("{}/api/v1/agent-ledger/actions", base_url()))
        .bearer_auth(&token)
        .json(&json!({
            "action": "rollout_restart",
            "params": {"cluster": "nano-bank", "deployment": "coo"},
            "effect": {"outcome": "executed", "effect": {"restarted_at": "t"}}
        }))
        .send()
        .await
        .unwrap();
    assert!(r.status().is_success(), "status {}", r.status());
    let body: Value = r.json().await.unwrap();
    assert!(body["seq"].as_i64().is_some(), "seq missing: {body}");
    assert!(body["entry_hash"].as_str().is_some(), "entry_hash missing: {body}");
}

#[tokio::test]
async fn rejects_a_request_without_a_service_token() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("skip: stack down");
        return;
    }
    let r = c
        .post(format!("{}/api/v1/agent-ledger/actions", base_url()))
        .json(&json!({"action": "x", "params": {}, "effect": {}}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status().as_u16(), 401, "unauthenticated must be rejected");
}

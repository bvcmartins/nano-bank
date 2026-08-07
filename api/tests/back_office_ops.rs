//! Integration tests for the service-plane back-office operational reads.
//!
//! Graceful-skip harness (mirrors tests/finance.rs): every test probes
//! `GET /health` and returns early (still passing) when the API is unreachable,
//! so `cargo test` passes with nothing running. No GL core needed — these are
//! reads. Run against a live stack:
//!   cd api && cargo test --test back_office_ops -- --nocapture
//! Override the base URL with NANO_BANK_TEST_URL.

use serde_json::{json, Value};
use uuid::Uuid;

const TEST_PASSWORD: &str = "securepass123";
// Dev service-plane secret (api/config/default.toml). Overridable in CI.
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
    let resp = c
        .post(format!("{}/api/v1/auth/service-token", base_url()))
        .json(&json!({ "client_secret": SERVICE_SECRET }))
        .send()
        .await
        .expect("service-token request");
    assert!(
        resp.status().is_success(),
        "service-token: {}",
        resp.status()
    );
    resp.json::<Value>().await.unwrap()["access_token"]
        .as_str()
        .unwrap()
        .to_string()
}

// A logged-in customer token, to prove the service plane rejects it. Uses the
// same registration payload shape as tests/finance.rs (phone_number + sin).
async fn customer_token(c: &reqwest::Client) -> String {
    let n = Uuid::new_v4().as_u128();
    let email = format!("botest_{}@example.com", n % 1_000_000_000);
    let reg = c
        .post(format!("{}/api/v1/customers", base_url()))
        .json(&json!({
            "email": email,
            "phone_number": format!("{:010}", n % 10_000_000_000u128),
            "first_name": "Bo",
            "last_name": "Tester",
            "date_of_birth": "1990-01-01",
            "sin": format!("{:09}", n % 1_000_000_000),
            "password": TEST_PASSWORD
        }))
        .send()
        .await
        .expect("register");
    assert!(reg.status().is_success(), "register: {}", reg.status());
    let resp = c
        .post(format!("{}/api/v1/auth/login", base_url()))
        .json(&json!({ "email": email, "password": TEST_PASSWORD }))
        .send()
        .await
        .expect("login");
    assert!(resp.status().is_success(), "login: {}", resp.status());
    resp.json::<Value>().await.unwrap()["access_token"]
        .as_str()
        .unwrap()
        .to_string()
}

#[tokio::test]
async fn float_returns_system_accounts_for_a_service_token() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!("{}/api/v1/back-office/ops/float", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("float request");
    assert!(resp.status().is_success(), "float: {}", resp.status());

    let body = resp.json::<Value>().await.unwrap();
    let accounts = body["accounts"].as_array().expect("accounts array");
    assert!(
        !accounts.is_empty(),
        "expected the bootstrapped system accounts"
    );
    assert!(
        accounts.iter().any(|a| a["system"] == "system"),
        "expected a system@ (VISA_CLEARING/BANK_SETTLEMENT) entry, got {accounts:?}"
    );
    assert!(
        body["total_float"].is_string(),
        "total_float should be a decimal string"
    );
}

#[tokio::test]
async fn float_rejects_a_customer_token() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let cust = customer_token(&c).await;

    let resp = c
        .get(format!("{}/api/v1/back-office/ops/float", base_url()))
        .bearer_auth(&cust)
        .send()
        .await
        .expect("float request");
    assert_eq!(
        resp.status().as_u16(),
        403,
        "customer token must be refused on the service plane"
    );
}

#[tokio::test]
async fn transactions_summary_returns_grouped_shape() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!(
            "{}/api/v1/back-office/ops/transactions?window=7d",
            base_url()
        ))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("transactions request");
    assert!(
        resp.status().is_success(),
        "transactions: {}",
        resp.status()
    );

    let body = resp.json::<Value>().await.unwrap();
    assert_eq!(body["window"], "7d");
    assert!(
        body["since"].is_string(),
        "since should be an rfc3339 string"
    );
    assert!(body["groups"].is_array(), "groups should be an array");
}

#[tokio::test]
async fn transactions_summary_rejects_bad_window() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!(
            "{}/api/v1/back-office/ops/transactions?window=1y",
            base_url()
        ))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("transactions request");
    assert_eq!(
        resp.status().as_u16(),
        400,
        "unsupported window must be a 400"
    );
}

#[tokio::test]
async fn rails_summary_returns_per_rail_groups() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!(
            "{}/api/v1/back-office/ops/rails?window=30d",
            base_url()
        ))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("rails request");
    assert!(resp.status().is_success(), "rails: {}", resp.status());

    let body = resp.json::<Value>().await.unwrap();
    assert_eq!(body["window"], "30d");
    assert!(
        body["since"].is_string(),
        "since should be an rfc3339 string"
    );
    assert!(
        body["rails"]["interac"].is_array(),
        "interac group should be an array"
    );
    assert!(
        body["rails"]["aft"].is_array(),
        "aft group should be an array"
    );
    assert!(
        body["rails"]["lynx"].is_array(),
        "lynx group should be an array"
    );
}

#[tokio::test]
async fn exceptions_summary_returns_recorded_counts() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!(
            "{}/api/v1/back-office/ops/exceptions?window=30d",
            base_url()
        ))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("exceptions request");
    assert!(resp.status().is_success(), "exceptions: {}", resp.status());

    let body = resp.json::<Value>().await.unwrap();
    assert_eq!(body["window"], "30d");
    assert!(
        body["since"].is_string(),
        "since should be an rfc3339 string"
    );
    let ex = &body["exceptions"];
    for k in [
        "failed_transactions",
        "reversals",
        "returned_aft_entries",
        "rejected_aft_entries",
        "wire_recalls",
    ] {
        assert!(
            ex[k].is_u64(),
            "exceptions.{k} should be a count, got {:?}",
            ex[k]
        );
    }
}

#[tokio::test]
async fn cards_summary_returns_holds_and_txn_groups() {
    let c = client();
    if !stack_up(&c).await {
        eprintln!("stack down; skipping");
        return;
    }
    let svc = service_token(&c).await;

    let resp = c
        .get(format!(
            "{}/api/v1/back-office/ops/cards?window=30d",
            base_url()
        ))
        .bearer_auth(&svc)
        .send()
        .await
        .expect("cards request");
    assert!(resp.status().is_success(), "cards: {}", resp.status());

    let body = resp.json::<Value>().await.unwrap();
    assert_eq!(body["window"], "30d");
    assert!(
        body["since"].is_string(),
        "since should be an rfc3339 string"
    );
    assert!(
        body["authorization_holds"]["open_count"].is_u64(),
        "open_count should be a count"
    );
    assert!(
        body["authorization_holds"]["open_amount"].is_string(),
        "open_amount should be a decimal string"
    );
    assert!(
        body["card_transactions"].is_array(),
        "card_transactions should be an array"
    );
}

#[tokio::test]
async fn declines_returns_bucketed_shape_for_a_service_token() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let svc = service_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/declines?window=30d", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let body: serde_json::Value = r.json().await.unwrap();
    assert!(body.get("by_category").is_some());
    assert!(body.get("by_channel").is_some());
    // The fraud bucket must never surface: 'risk' is folded to 'other'.
    assert!(body["by_category"].get("risk").is_none());
}

#[tokio::test]
async fn declines_rejects_a_customer_token() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let cust = customer_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/declines?window=30d", base_url()))
        .bearer_auth(&cust)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status().as_u16(), 403,
        "customer token must be refused on the service plane");
}

#[tokio::test]
async fn cards_summary_now_carries_auth_rates() {
    let c = client();
    if !stack_up(&c).await {
        return;
    }
    let svc = service_token(&c).await;
    let r = c
        .get(format!("{}/api/v1/back-office/ops/cards?window=30d", base_url()))
        .bearer_auth(&svc)
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let body: serde_json::Value = r.json().await.unwrap();
    assert!(body["rates"].get("approved").is_some());
    assert!(body["rates"].get("decline_rate").is_some());
}

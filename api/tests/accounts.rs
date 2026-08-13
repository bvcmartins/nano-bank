//! Integration tests for account creation.
//!
//! Drives the **real HTTP surface** of a running stack (the API on `:8081` +
//! the Kind Postgres), because the package is a binary (its items aren't
//! importable here).
//!
//! Every test probes `GET /health` first and **returns early (skips) when the
//! API is unreachable**, so `cargo test` still passes with nothing running.
//!
//! Run against a live stack:
//! ```bash
//! cd api && cargo test --test accounts -- --nocapture
//! ```
//! Override the base URL with `NANO_BANK_TEST_URL`.

use serde_json::{json, Value};
use uuid::Uuid;

const TEST_PASSWORD: &str = "securepass123";

fn base_url() -> String {
    std::env::var("NANO_BANK_TEST_URL").unwrap_or_else(|_| "http://localhost:8081".to_string())
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

macro_rules! require_stack {
    ($c:expr) => {
        if !stack_up($c).await {
            eprintln!("SKIP: nano-bank not reachable at {}", base_url());
            return;
        }
    };
}

/// Sign up a fresh customer and log in, returning a bearer token.
async fn session(c: &reqwest::Client) -> String {
    let n = Uuid::new_v4().as_u128();
    let email = format!("accttest_{}@example.com", n % 1_000_000_000);
    let body = json!({
        "email": email,
        "phone_number": format!("{:010}", (n % 10_000_000_000u128)),
        "first_name": "Acct",
        "last_name": "Test",
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

    let resp = c
        .post(format!("{}/api/v1/auth/login", base_url()))
        .json(&json!({ "email": email, "password": TEST_PASSWORD }))
        .send()
        .await
        .unwrap();
    assert!(resp.status().is_success(), "login: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    v["access_token"]
        .as_str()
        .expect("login response has an access_token")
        .to_string()
}

async fn open_account(c: &reqwest::Client, token: &str, body: Value) -> reqwest::Response {
    c.post(format!("{}/api/v1/accounts", base_url()))
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
        .unwrap()
}

#[tokio::test]
async fn account_creation_is_idempotent() {
    let c = client();
    require_stack!(&c);
    let token = session(&c).await;
    let key = format!("idem-{}", Uuid::new_v4());
    let body = json!({ "account_type": "chequing", "idempotency_key": key });

    let r1 = open_account(&c, &token, body.clone()).await;
    assert_eq!(r1.status().as_u16(), 201);
    let v1: Value = r1.json().await.unwrap();

    let r2 = open_account(&c, &token, body).await;
    assert_eq!(
        r2.status().as_u16(),
        200,
        "replay should be 200, not a new open"
    );
    let v2: Value = r2.json().await.unwrap();

    assert_eq!(
        v1["account_id"], v2["account_id"],
        "same account returned, not a second one opened"
    );
}

#[tokio::test]
async fn same_customer_can_reuse_the_account_type_with_a_different_key() {
    let c = client();
    require_stack!(&c);
    let token = session(&c).await;

    let r1 = open_account(
        &c,
        &token,
        json!({ "account_type": "chequing", "idempotency_key": format!("a-{}", Uuid::new_v4()) }),
    )
    .await;
    assert_eq!(r1.status().as_u16(), 201);
    let v1: Value = r1.json().await.unwrap();

    let r2 = open_account(
        &c,
        &token,
        json!({ "account_type": "chequing", "idempotency_key": format!("b-{}", Uuid::new_v4()) }),
    )
    .await;
    assert_eq!(r2.status().as_u16(), 201);
    let v2: Value = r2.json().await.unwrap();

    assert_ne!(
        v1["account_id"], v2["account_id"],
        "different keys must open distinct accounts"
    );
}

#[tokio::test]
async fn account_creation_without_a_key_still_works() {
    let c = client();
    require_stack!(&c);
    let token = session(&c).await;

    let resp = open_account(&c, &token, json!({ "account_type": "savings" })).await;
    assert_eq!(resp.status().as_u16(), 201);
}

//! Integration tests for the Interac e-Transfer rail.
//!
//! Same offline-skip harness as `tests/transactions.rs`: each test probes
//! `GET /health` and **returns early (skips) when the API is unreachable**, so
//! `cargo test` still passes with nothing running. Money movement posts to the
//! GL core, so tests skip when a funding deposit returns `503` (core down).
//!
//! Run against a live stack:
//! ```bash
//! cd api && cargo test --test interac -- --nocapture
//! ```
//! Override the base URL with `NANO_BANK_TEST_URL`.
//!
//! Reachability note: `register_autodeposit` always sets an autodeposit account,
//! so a *registered* handle always autodeposits. The **inbound-held** path
//! (registered-without-autodeposit) is therefore not reachable through the public
//! API in v1 (see the repo `CLAUDE.md` "Known v1 gaps") and is exercised by the
//! Python network simulator instead — out of scope for these HTTP tests. The
//! "available" (held-for-claim) transfers used below are the **external** kind
//! (unregistered recipient handle), whose `recipient_customer_id` is NULL.

use serde_json::{json, Value};
use uuid::Uuid;

mod common;

use common::*;

/// A funded chequing account, or `None` if the GL core isn't available (503).
async fn funded_account(c: &reqwest::Client, token: &str, amount: f64) -> Option<Uuid> {
    let account = create_account(c, token, "chequing").await;
    let resp = post_json(
        c,
        token,
        "/api/v1/transactions/deposit",
        json!({ "account_id": account, "amount": amount, "description": "seed" }),
    )
    .await;
    if resp.status().as_u16() == 503 {
        eprintln!("SKIP: GL core unavailable (deposit returned 503)");
        return None;
    }
    assert!(resp.status().is_success(), "deposit: {}", resp.status());
    Some(account)
}

async fn get_etransfer(c: &reqwest::Client, token: &str, id: Uuid) -> Value {
    c.get(format!("{}/api/v1/interac/etransfers/{}", base_url(), id))
        .bearer_auth(token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap()
}

/// Send an e-Transfer; returns the response so the caller can branch on 503/status.
async fn send_etransfer(c: &reqwest::Client, token: &str, body: Value) -> reqwest::Response {
    post_json(c, token, "/api/v1/interac/etransfers", body).await
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

#[tokio::test]
async fn send_requires_auth() {
    let c = client();
    require_stack!(&c);
    let resp = c
        .post(format!("{}/api/v1/interac/etransfers", base_url()))
        .json(&json!({
            "from_account_id": Uuid::new_v4(),
            "amount": 10.0,
            "recipient_handle_type": "email",
            "recipient_handle_value": "nobody@example.com"
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(
        resp.status().as_u16(),
        401,
        "unauthenticated send must be 401"
    );
}

// ---------------------------------------------------------------------------
// Autodeposit: send to a registered handle deposits immediately
// ---------------------------------------------------------------------------

#[tokio::test]
async fn send_to_autodeposit_handle_credits_recipient() {
    let c = client();
    require_stack!(&c);
    let (_a, a_token) = session(&c).await;
    let from = match funded_account(&c, &a_token, 500.0).await {
        Some(a) => a,
        None => return,
    };

    // Recipient B registers an autodeposit handle.
    let (_b, b_token) = session(&c).await;
    let b_acct = create_account(&c, &b_token, "chequing").await;
    let handle = format!("bob_{}@example.com", rnd() % 1_000_000_000);
    let reg = post_json(
        &c,
        &b_token,
        "/api/v1/interac/autodeposit",
        json!({ "handle_type": "email", "handle_value": handle, "deposit_account_id": b_acct }),
    )
    .await;
    assert!(
        reg.status().is_success(),
        "register autodeposit: {}",
        reg.status()
    );

    let resp = send_etransfer(
        &c,
        &a_token,
        json!({ "from_account_id": from, "amount": 120.0,
                "recipient_handle_type": "email", "recipient_handle_value": handle }),
    )
    .await;
    assert!(resp.status().is_success(), "send: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    assert_eq!(v["status"], "deposited", "autodeposit should deposit: {v}");
    assert_eq!(balance(&c, &b_token, b_acct).await, 120.0);
}

// ---------------------------------------------------------------------------
// Held-for-claim (external recipient): send -> claim with Q&A
// ---------------------------------------------------------------------------

/// Send an "available" external transfer with a security Q&A; returns its id, or
/// `None` if the core is down.
async fn send_available(
    c: &reqwest::Client,
    a_token: &str,
    from: Uuid,
    amount: f64,
    handle: &str,
    answer: &str,
) -> Option<Uuid> {
    let resp = send_etransfer(
        c,
        a_token,
        json!({ "from_account_id": from, "amount": amount,
                "recipient_handle_type": "email", "recipient_handle_value": handle,
                "security_question": "pet?", "security_answer": answer }),
    )
    .await;
    if resp.status().as_u16() == 503 {
        eprintln!("SKIP: GL core unavailable (send returned 503)");
        return None;
    }
    assert!(resp.status().is_success(), "send: {}", resp.status());
    let v: Value = resp.json().await.unwrap();
    assert_eq!(
        v["status"], "available",
        "held transfer should be available: {v}"
    );
    Some(Uuid::parse_str(v["etransfer_id"].as_str().unwrap()).unwrap())
}

#[tokio::test]
async fn send_then_claim_with_correct_answer_deposits() {
    let c = client();
    require_stack!(&c);
    let (_a, a_token) = session(&c).await;
    let from = match funded_account(&c, &a_token, 500.0).await {
        Some(a) => a,
        None => return,
    };
    let handle = format!("ext_{}@other.test", rnd() % 1_000_000_000);
    let id = match send_available(&c, &a_token, from, 75.0, &handle, "rex").await {
        Some(id) => id,
        None => return,
    };

    // Recipient B claims by answering the question.
    let (_b, b_token) = session(&c).await;
    let b_acct = create_account(&c, &b_token, "chequing").await;
    let resp = post_json(
        &c,
        &b_token,
        &format!("/api/v1/interac/etransfers/{}/claim", id),
        json!({ "security_answer": "REX", "deposit_account_id": b_acct }),
    )
    .await;
    assert!(resp.status().is_success(), "claim: {}", resp.status());
    let claimed: Value = resp.json().await.unwrap();
    assert_eq!(
        claimed["status"], "deposited",
        "claim should deposit: {claimed}"
    );
    assert_eq!(balance(&c, &b_token, b_acct).await, 75.0);

    // Claiming records B as the recipient, so B retains the receipt: a GET by B
    // now returns the transfer (before the fix this 404'd, because an external
    // transfer's recipient_customer_id was left NULL).
    let get = c
        .get(format!("{}/api/v1/interac/etransfers/{}", base_url(), id))
        .bearer_auth(&b_token)
        .send()
        .await
        .unwrap();
    assert_eq!(
        get.status().as_u16(),
        200,
        "claimant should see the transfer after claiming"
    );
    let seen: Value = get.json().await.unwrap();
    assert_eq!(
        seen["status"], "deposited",
        "claimant's GET should show deposited: {seen}"
    );

    // ...and it appears in B's e-Transfer history.
    let list: Value = c
        .get(format!("{}/api/v1/interac/etransfers", base_url()))
        .bearer_auth(&b_token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(
        list.as_array()
            .unwrap()
            .iter()
            .any(|e| e["etransfer_id"] == claimed["etransfer_id"]),
        "claimed transfer should appear in B's list: {list}"
    );
}

#[tokio::test]
async fn claim_locks_after_three_wrong_answers() {
    let c = client();
    require_stack!(&c);
    let (_a, a_token) = session(&c).await;
    let from = match funded_account(&c, &a_token, 500.0).await {
        Some(a) => a,
        None => return,
    };
    let handle = format!("ext_{}@other.test", rnd() % 1_000_000_000);
    let id = match send_available(&c, &a_token, from, 40.0, &handle, "rex").await {
        Some(id) => id,
        None => return,
    };

    let (_b, b_token) = session(&c).await;
    let b_acct = create_account(&c, &b_token, "chequing").await;
    let wrong = json!({ "security_answer": "wrong", "deposit_account_id": b_acct });
    for attempt in 1..=3 {
        let resp = post_json(
            &c,
            &b_token,
            &format!("/api/v1/interac/etransfers/{}/claim", id),
            wrong.clone(),
        )
        .await;
        let code = resp.status().as_u16();
        if attempt < 3 {
            assert_eq!(code, 400, "wrong answer #{attempt} should be 400");
        } else {
            // The lock is an authorization failure (AppError::Authorization -> 403).
            assert_eq!(code, 403, "third wrong answer should lock (403)");
        }
    }
    // Check via the sender's token (an external recipient can't GET it).
    assert_eq!(get_etransfer(&c, &a_token, id).await["status"], "failed");
}

// ---------------------------------------------------------------------------
// Ownership: decline (recipient-only) and cancel (sender-only)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn decline_by_non_recipient_is_404() {
    let c = client();
    require_stack!(&c);
    let (_a, a_token) = session(&c).await;
    let from = match funded_account(&c, &a_token, 500.0).await {
        Some(a) => a,
        None => return,
    };
    // External "available" transfer: recipient_customer_id is NULL, so NO
    // authenticated customer may decline it (the fix). Pre-fix, any customer
    // could. A third, unrelated customer must get 404.
    let handle = format!("ext_{}@other.test", rnd() % 1_000_000_000);
    let id = match send_available(&c, &a_token, from, 20.0, &handle, "rex").await {
        Some(id) => id,
        None => return,
    };
    let (_c3, c3_token) = session(&c).await;
    let resp = post_json(
        &c,
        &c3_token,
        &format!("/api/v1/interac/etransfers/{}/decline", id),
        json!({}),
    )
    .await;
    assert_eq!(
        resp.status().as_u16(),
        404,
        "non-recipient decline must be 404"
    );
    // The transfer is untouched (still available).
    assert_eq!(get_etransfer(&c, &a_token, id).await["status"], "available");
}

#[tokio::test]
async fn cancel_by_non_sender_is_404_but_sender_can_cancel() {
    let c = client();
    require_stack!(&c);
    let (_a, a_token) = session(&c).await;
    let from = match funded_account(&c, &a_token, 500.0).await {
        Some(a) => a,
        None => return,
    };
    let handle = format!("ext_{}@other.test", rnd() % 1_000_000_000);
    let id = match send_available(&c, &a_token, from, 30.0, &handle, "rex").await {
        Some(id) => id,
        None => return,
    };

    // A stranger cannot cancel A's transfer.
    let (_b, b_token) = session(&c).await;
    let resp = post_json(
        &c,
        &b_token,
        &format!("/api/v1/interac/etransfers/{}/cancel", id),
        json!({}),
    )
    .await;
    assert_eq!(resp.status().as_u16(), 404, "non-sender cancel must be 404");

    // The sender can, and gets the funds back.
    let resp = post_json(
        &c,
        &a_token,
        &format!("/api/v1/interac/etransfers/{}/cancel", id),
        json!({}),
    )
    .await;
    assert!(
        resp.status().is_success(),
        "sender cancel: {}",
        resp.status()
    );
    assert_eq!(get_etransfer(&c, &a_token, id).await["status"], "cancelled");
    // Funds returned: started 500; the send debits the $30 hold plus the flat
    // $1.50 outgoing e-transfer fee (500 -> 468.50); cancelling refunds the $30
    // hold but *not* the fee (the fee is earned when the transfer is sent), so the
    // sender lands at 468.50 + 30 = 498.50.
    assert_eq!(balance(&c, &a_token, from).await, 498.5);
}

// ---------------------------------------------------------------------------
// Notification-outbox drainer (admin plane)
// ---------------------------------------------------------------------------
// The `interac_notifications` outbox is written by the lifecycle handlers but had
// no consumer. `POST /admin/flush-notifications` (service token) is the drainer:
// it claims undelivered rows race-safely, hands each to the delivery channel
// (stubbed), and marks them delivered — dead-lettering a row once it exhausts its
// retry budget so a permanently-failing send can't be retried forever.

/// Direct DB handle for seeding/asserting outbox rows (bypasses the rail so these
/// tests don't need a running core). Mirrors the helper in `agents.rs`.
async fn test_db() -> Option<sqlx::PgPool> {
    let url = std::env::var("NANO_BANK_TEST_DB_URL").unwrap_or_else(|_| {
        "postgres://nanobank_user:secure_nano_password_2024!@[::1]:5432/nano_bank_db".to_string()
    });
    match sqlx::PgPool::connect(&url).await {
        Ok(pool) => Some(pool),
        Err(e) => {
            eprintln!("SKIP: DB unreachable ({e})");
            None
        }
    }
}

/// Mint a network/admin-plane service token (same path the Python simulators use).
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

/// A minimal parent e-Transfer row (only the NOT-NULL-without-default columns),
/// so a notification has a valid `etransfer_id` FK without driving the rail.
async fn seed_etransfer(db: &sqlx::PgPool, handle: &str) -> Uuid {
    sqlx::query_scalar(
        "INSERT INTO interac_etransfers \
           (direction, amount, recipient_handle_type, recipient_handle_value, claim_token) \
         VALUES ('outbound', 50.00, 'email', $1, $2) RETURNING etransfer_id",
    )
    .bind(handle)
    .bind(format!("ct{:x}", rnd()))
    .fetch_one(db)
    .await
    .unwrap()
}

/// Seeds an outbox row at a given attempt count. `attempts` is set in the *same*
/// statement as the insert on purpose: seeding at 0 and bumping afterwards leaves a
/// window in which a concurrent drainer (a sibling test, or the every-5-minute
/// CronJob against the same database) can claim and deliver the row before it is
/// marked exhausted.
async fn seed_notification(
    db: &sqlx::PgPool,
    etransfer_id: Uuid,
    handle: &str,
    attempts: i32,
) -> Uuid {
    sqlx::query_scalar(
        "INSERT INTO interac_notifications \
           (etransfer_id, handle_value, kind, message, delivery_attempts) \
         VALUES ($1, $2, 'deposit_completed', 'test drain', $3) RETURNING notification_id",
    )
    .bind(etransfer_id)
    .bind(handle)
    .bind(attempts)
    .fetch_one(db)
    .await
    .unwrap()
}

async fn flush_notifications(c: &reqwest::Client, token: &str) -> reqwest::Response {
    c.post(format!(
        "{}/api/v1/interac/admin/flush-notifications",
        base_url()
    ))
    .bearer_auth(token)
    .send()
    .await
    .unwrap()
}

/// One flush covers both properties, so it stays deterministic on the shared
/// outbox (two sibling tests each calling the *global* drain would race — a
/// concurrent drainer can claim your row first and still be mid-send when you
/// check). A fresh row is delivered + stamped; a retry-exhausted row is left
/// untouched — not delivered, not re-attempted — so a permanently-failing send
/// dead-letters instead of looping forever.
#[tokio::test]
async fn flush_notifications_delivers_fresh_and_dead_letters_exhausted() {
    let c = client();
    require_stack!(&c);
    let Some(db) = test_db().await else { return };

    // Fresh, deliverable.
    let h_fresh = format!("drain-{}@nano.test", rnd());
    let et_fresh = seed_etransfer(&db, &h_fresh).await;
    let fresh = seed_notification(&db, et_fresh, &h_fresh, 0).await;

    // Exhausted its retry budget — born at the cap, never claimable.
    let h_dead = format!("dead-{}@nano.test", rnd());
    let et_dead = seed_etransfer(&db, &h_dead).await;
    let dead = seed_notification(&db, et_dead, &h_dead, 5).await;

    let tok = service_token(&c).await;
    let resp = flush_notifications(&c, &tok).await;
    assert!(resp.status().is_success(), "flush: {}", resp.status());

    let (delivered, has_ts): (bool, bool) = sqlx::query_as(
        "SELECT delivered, delivered_at IS NOT NULL \
         FROM interac_notifications WHERE notification_id = $1",
    )
    .bind(fresh)
    .fetch_one(&db)
    .await
    .unwrap();
    assert!(
        delivered,
        "fresh notification should be delivered after a flush"
    );
    assert!(has_ts, "delivered_at should be stamped");

    let (delivered, attempts): (bool, i32) = sqlx::query_as(
        "SELECT delivered, delivery_attempts \
         FROM interac_notifications WHERE notification_id = $1",
    )
    .bind(dead)
    .fetch_one(&db)
    .await
    .unwrap();
    assert!(!delivered, "exhausted row must not be delivered");
    assert_eq!(attempts, 5, "exhausted row must not be re-attempted");
}

/// A service token is the wrong plane's? No — the drainer is admin-only: a
/// customer token must be rejected (no accidental customer-triggered flush).
#[tokio::test]
async fn flush_notifications_rejects_customer_token() {
    let c = client();
    require_stack!(&c);
    let (_id, token) = session(&c).await;
    let resp = flush_notifications(&c, &token).await;
    assert_eq!(
        resp.status().as_u16(),
        403,
        "customer token must be 403 on the admin plane"
    );
}

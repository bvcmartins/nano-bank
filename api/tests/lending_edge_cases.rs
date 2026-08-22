//! Edge-case integration tests for the Lending Subsystem.
//!
//! Reuses the Kind DB / API infrastructure established by `api/tests/common/mod.rs`.

mod common;

use common::*;
use serde_json::{json, Value};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Authorization Boundaries: B cannot access A's loan
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_unauthorized_loan_access() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    // 1. Setup Customer A with an active loan
    let (customer_a_id, email_a) = create_customer(&c).await;
    let token_a = login(&c, &email_a).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_a_id)
        .execute(&pool)
        .await
        .unwrap();

    let _chequing_a = create_account(&c, &token_a, "chequing").await;

    let apply_resp = post_json(
        &c,
        &token_a,
        "/api/v1/loans",
        json!({
            "principal_amount": 5000.00,
            "interest_rate": 0.05,
            "amortization_months": 12
        }),
    )
    .await;
    assert_eq!(apply_resp.status(), reqwest::StatusCode::CREATED);
    let loan_res: Value = apply_resp.json().await.unwrap();
    let loan_id = Uuid::parse_str(loan_res["loan_id"].as_str().unwrap()).unwrap();

    // 2. Setup Customer B
    let (customer_b_id, email_b) = create_customer(&c).await;
    let token_b = login(&c, &email_b).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_b_id)
        .execute(&pool)
        .await
        .unwrap();

    // 3. Customer B attempts to view Customer A's loan -> 404 Not Found
    let get_resp = c
        .get(format!("{}/api/v1/loans/{}", base_url(), loan_id))
        .bearer_auth(&token_b)
        .send()
        .await
        .unwrap();
    assert_eq!(get_resp.status(), reqwest::StatusCode::NOT_FOUND);

    // 4. Customer B attempts to disburse Customer A's loan -> 404 Not Found
    let disburse_resp = c
        .post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token_b)
        .send()
        .await
        .unwrap();
    assert_eq!(disburse_resp.status(), reqwest::StatusCode::NOT_FOUND);

    // 5. Customer B attempts to repay Customer A's loan -> 404 Not Found
    let repay_resp = post_json(
        &c,
        &token_b,
        &format!("/api/v1/loans/{}/repay", loan_id),
        json!({
            "funding_account_id": Uuid::new_v4(),
            "amount": 100.00
        }),
    )
    .await;
    assert_eq!(repay_resp.status(), reqwest::StatusCode::NOT_FOUND);
}

// ---------------------------------------------------------------------------
// Block Double Disbursement
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_double_disbursement_blocked() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    let (customer_id, email) = create_customer(&c).await;
    let token = login(&c, &email).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_id)
        .execute(&pool)
        .await
        .unwrap();

    let _chequing = create_account(&c, &token, "chequing").await;

    // Apply for loan
    let apply_resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 2000.00,
            "interest_rate": 0.06,
            "amortization_months": 12
        }),
    )
    .await;
    assert_eq!(apply_resp.status(), reqwest::StatusCode::CREATED);
    let loan_res: Value = apply_resp.json().await.unwrap();
    let loan_id = Uuid::parse_str(loan_res["loan_id"].as_str().unwrap()).unwrap();

    // First disbursement -> 200 OK
    let disburse_1 = c
        .post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap();
    assert_eq!(disburse_1.status(), reqwest::StatusCode::OK);

    // Second disbursement -> 400 Bad Request ("Loan is not pending disbursement")
    let disburse_2 = c
        .post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap();
    assert_eq!(disburse_2.status(), reqwest::StatusCode::BAD_REQUEST);
    let err_val: Value = disburse_2.json().await.unwrap();
    assert!(
        err_val["error"]["message"]
            .as_str()
            .unwrap()
            .contains("not pending disbursement"),
        "error should indicate loan is not pending disbursement"
    );
}

// ---------------------------------------------------------------------------
// Insufficient Funds on Repayment
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_insufficient_funds_on_repayment() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    let (customer_id, email) = create_customer(&c).await;
    let token = login(&c, &email).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_id)
        .execute(&pool)
        .await
        .unwrap();

    let chequing = create_account(&c, &token, "chequing").await;

    // Apply for loan
    let apply_resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 5000.00,
            "interest_rate": 0.05,
            "amortization_months": 12
        }),
    )
    .await;
    let loan_res: Value = apply_resp.json().await.unwrap();
    let loan_id = Uuid::parse_str(loan_res["loan_id"].as_str().unwrap()).unwrap();

    // Disburse loan (adds $5000 to chequing)
    c.post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap();

    // The default daily_withdrawal_limit ($1,000, on account_limits) is well
    // under the $5,000 disbursement, and this test isn't exercising withdrawal
    // limits, so raise it directly rather than drip-withdrawing in $1,000
    // increments.
    sqlx::query(
        "INSERT INTO account_limits (account_id, daily_withdrawal_limit) VALUES ($1, 10000.00) \
         ON CONFLICT (account_id) DO UPDATE SET daily_withdrawal_limit = 10000.00",
    )
    .bind(chequing)
    .execute(&pool)
    .await
    .unwrap();

    // Withdraw the disbursed money to ensure chequing balance is 0.00
    let withdraw_resp = post_json(
        &c,
        &token,
        "/api/v1/transactions/withdrawal",
        json!({
            "account_id": chequing,
            "amount": 5000.00,
            "description": "cash out"
        }),
    )
    .await;
    assert_eq!(withdraw_resp.status(), reqwest::StatusCode::CREATED);

    // Attempt to repay $100.00 from empty chequing account -> 400 Bad Request / INSUFFICIENT_FUNDS
    let repay_resp = post_json(
        &c,
        &token,
        &format!("/api/v1/loans/{}/repay", loan_id),
        json!({
            "funding_account_id": chequing,
            "amount": 100.00
        }),
    )
    .await;
    assert_eq!(repay_resp.status(), reqwest::StatusCode::BAD_REQUEST);
    let err_val: Value = repay_resp.json().await.unwrap();
    assert_eq!(
        err_val["error"]["code"].as_str().unwrap(),
        "INSUFFICIENT_FUNDS"
    );
}

// ---------------------------------------------------------------------------
// Overpayment Protection: Repaying more than the remaining debt is capped
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_overpayment_protection() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    let (customer_id, email) = create_customer(&c).await;
    let token = login(&c, &email).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_id)
        .execute(&pool)
        .await
        .unwrap();

    let chequing = create_account(&c, &token, "chequing").await;

    // Apply for a loan of $1,000.00
    let apply_resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 1000.00,
            "interest_rate": 0.05,
            "amortization_months": 12
        }),
    )
    .await;
    let loan_res: Value = apply_resp.json().await.unwrap();
    let loan_id = Uuid::parse_str(loan_res["loan_id"].as_str().unwrap()).unwrap();
    let loan_account_id = Uuid::parse_str(loan_res["account_id"].as_str().unwrap()).unwrap();

    // Disburse loan (adds $1000 to chequing, debt of $1000 on loan account)
    c.post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap();

    // Now, deposit another $1000.00 to chequing to make the funding balance $2000.00
    let deposit_resp = post_json(
        &c,
        &token,
        "/api/v1/transactions/deposit",
        json!({
            "account_id": chequing,
            "amount": 1000.00,
            "description": "extra funding"
        }),
    )
    .await;
    assert_eq!(deposit_resp.status(), reqwest::StatusCode::CREATED);

    // Verify chequing balance is $2000.00
    let bal_val: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            chequing
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&bal_val["balance"]), 2000.00);

    // Attempt to repay $1,500.00 on the $1,000.00 debt
    let repay_resp = post_json(
        &c,
        &token,
        &format!("/api/v1/loans/{}/repay", loan_id),
        json!({
            "funding_account_id": chequing,
            "amount": 1500.00
        }),
    )
    .await;
    assert_eq!(repay_resp.status(), reqwest::StatusCode::OK);
    let repay_res: Value = repay_resp.json().await.unwrap();
    assert_eq!(repay_res["status"].as_str().unwrap(), "closed");

    // The loan account should be fully paid off (balance = 0.00)
    let loan_bal: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            loan_account_id
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&loan_bal["balance"]), 0.00);

    // Crucially, the funding chequing account should have been debited by exactly $1,000.00
    // (the capped remaining debt), NOT $1,500.00! Its balance should be $1,000.00.
    let chequing_bal: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            chequing
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&chequing_bal["balance"]), 1000.00);
}

// ---------------------------------------------------------------------------
// Input Validation Integrity
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_invalid_loan_application_parameters() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    let (customer_id, email) = create_customer(&c).await;
    let token = login(&c, &email).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_id)
        .execute(&pool)
        .await
        .unwrap();

    // 1. Invalid principal_amount (<= 0)
    let resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 0.00,
            "interest_rate": 0.05,
            "amortization_months": 12
        }),
    )
    .await;
    assert_eq!(resp.status(), reqwest::StatusCode::BAD_REQUEST);
    let err_val: Value = resp.json().await.unwrap();
    assert!(
        err_val["error"]["message"]
            .as_str()
            .unwrap()
            .contains("positive"),
        "error should state principal must be positive"
    );

    // 2. Invalid interest_rate (< 0 or > 1)
    let resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 1000.00,
            "interest_rate": 1.05,
            "amortization_months": 12
        }),
    )
    .await;
    assert_eq!(resp.status(), reqwest::StatusCode::BAD_REQUEST);
    let err_val: Value = resp.json().await.unwrap();
    assert!(
        err_val["error"]["message"]
            .as_str()
            .unwrap()
            .contains("between 0 and 1"),
        "error should state rate must be between 0 and 1"
    );

    // 3. Invalid amortization_months (<= 0)
    let resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 1000.00,
            "interest_rate": 0.05,
            "amortization_months": 0
        }),
    )
    .await;
    assert_eq!(resp.status(), reqwest::StatusCode::BAD_REQUEST);
    let err_val: Value = resp.json().await.unwrap();
    assert!(
        err_val["error"]["message"]
            .as_str()
            .unwrap()
            .contains("positive"),
        "error should state amortization months must be positive"
    );
}

// ---------------------------------------------------------------------------
// Interest Accrual only on Active Loans
// ---------------------------------------------------------------------------
#[tokio::test]
async fn test_interest_accrual_only_on_active_loans() {
    let c = client();
    require_stack!(&c);

    let Some(pool) = test_db().await else {
        println!("SKIP: direct DB connection unavailable");
        return;
    };

    let (customer_id, email) = create_customer(&c).await;
    let token = login(&c, &email).await;

    sqlx::query("UPDATE customers SET kyc_status = 'verified' WHERE customer_id = $1")
        .bind(customer_id)
        .execute(&pool)
        .await
        .unwrap();

    let _chequing = create_account(&c, &token, "chequing").await;

    // Apply for loan (ends up as status: 'pending_disbursement')
    let apply_resp = post_json(
        &c,
        &token,
        "/api/v1/loans",
        json!({
            "principal_amount": 10000.00,
            "interest_rate": 0.10,
            "amortization_months": 12
        }),
    )
    .await;
    let loan_res: Value = apply_resp.json().await.unwrap();
    let loan_id = Uuid::parse_str(loan_res["loan_id"].as_str().unwrap()).unwrap();
    let loan_account_id = Uuid::parse_str(loan_res["account_id"].as_str().unwrap()).unwrap();

    // Trigger interest accrual
    let svc_token = service_token(&c).await;
    let accrue_resp = c
        .post(format!("{}/api/v1/loans/admin/accrue", base_url()))
        .bearer_auth(&svc_token)
        .send()
        .await
        .unwrap();
    assert_eq!(accrue_resp.status(), reqwest::StatusCode::OK);

    // Verify loan account balance is still 0 (no interest accrued on pending disbursement loan)
    let loan_bal: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            loan_account_id
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&loan_bal["balance"]), 0.00);

    // Disburse the loan to make it active
    c.post(format!("{}/api/v1/loans/{}/disburse", base_url(), loan_id))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap();

    // Verify loan account balance is now -10000.00
    let loan_bal_active: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            loan_account_id
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&loan_bal_active["balance"]), -10000.00);

    // Trigger interest accrual again -> should accrue interest now (10,000 * 0.10 / 365 = 2.7397 -> rounds to 2.74)
    let accrue_resp = c
        .post(format!("{}/api/v1/loans/admin/accrue", base_url()))
        .bearer_auth(&svc_token)
        .send()
        .await
        .unwrap();
    assert_eq!(accrue_resp.status(), reqwest::StatusCode::OK);

    let loan_bal_accrued: Value = c
        .get(format!(
            "{}/api/v1/accounts/{}/balance",
            base_url(),
            loan_account_id
        ))
        .bearer_auth(&token)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(as_num(&loan_bal_accrued["balance"]), -10002.74);
}

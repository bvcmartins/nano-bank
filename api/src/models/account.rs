use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;
use validator::Validate;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "account_type", rename_all = "snake_case")]
pub enum AccountType {
    Checking,
    Savings,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "account_status", rename_all = "snake_case")]
pub enum AccountStatus {
    Active,
    Frozen,
    Closed,
    PendingActivation,
}

// Core Account Entity
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Account {
    pub account_id: Uuid,
    pub customer_id: Uuid,
    pub account_number: String,
    pub account_type: AccountType,
    pub currency: String,
    pub balance: Decimal,
    pub available_balance: Decimal,
    pub status: AccountStatus,
    pub interest_rate: Decimal,
    pub overdraft_limit: Decimal,
    pub minimum_balance: Decimal,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub activated_at: Option<DateTime<Utc>>,
    pub closed_at: Option<DateTime<Utc>>,
}

// Account creation request
#[derive(Debug, Serialize, Deserialize)]
pub struct CreateAccountRequest {
    pub account_type: AccountType,
    pub initial_deposit: Option<Decimal>,
}

// Account limits entity
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct AccountLimits {
    pub limit_id: Uuid,
    pub account_id: Uuid,
    pub daily_withdrawal_limit: Decimal,
    pub daily_transfer_limit: Decimal,
    pub monthly_transfer_limit: Decimal,
    pub annual_transfer_limit: Decimal,
    pub daily_withdrawal_used: Decimal,
    pub daily_transfer_used: Decimal,
    pub monthly_transfer_used: Decimal,
    pub annual_transfer_used: Decimal,
    pub last_reset_date: chrono::NaiveDate,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

// Account limits update request
#[derive(Debug, Serialize, Deserialize)]
pub struct UpdateAccountLimitsRequest {
    pub daily_withdrawal_limit: Option<Decimal>,
    pub daily_transfer_limit: Option<Decimal>,
    pub monthly_transfer_limit: Option<Decimal>,
    pub annual_transfer_limit: Option<Decimal>,
}

// Account holds entity
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct AccountHold {
    pub hold_id: Uuid,
    pub account_id: Uuid,
    pub amount: Decimal,
    pub reason: String,
    pub reference_id: Option<String>,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub released_at: Option<DateTime<Utc>>,
}

// Account hold creation request
#[derive(Debug, Serialize, Deserialize, Validate)]
pub struct CreateAccountHoldRequest {
    pub amount: Decimal,

    #[validate(length(min = 1, max = 255))]
    pub reason: String,

    pub reference_id: Option<String>,

    pub expires_at: DateTime<Utc>,
}

// Account balance inquiry response
#[derive(Debug, Serialize, Deserialize)]
pub struct AccountBalanceResponse {
    pub account_id: Uuid,
    pub account_number: String,
    pub balance: Decimal,
    pub available_balance: Decimal,
    pub currency: String,
    pub status: AccountStatus,
    pub holds: Vec<ActiveHold>,
}

// Active hold summary
#[derive(Debug, Serialize, Deserialize)]
pub struct ActiveHold {
    pub hold_id: Uuid,
    pub amount: Decimal,
    pub reason: String,
    pub expires_at: DateTime<Utc>,
}

// Account response (public view)
#[derive(Debug, Serialize, Deserialize)]
pub struct AccountResponse {
    pub account_id: Uuid,
    pub account_number: String,
    pub account_type: AccountType,
    pub currency: String,
    pub balance: Decimal,
    pub available_balance: Decimal,
    pub status: AccountStatus,
    pub created_at: DateTime<Utc>,
    pub activated_at: Option<DateTime<Utc>>,
}

impl From<Account> for AccountResponse {
    fn from(account: Account) -> Self {
        Self {
            account_id: account.account_id,
            account_number: account.account_number,
            account_type: account.account_type,
            currency: account.currency,
            balance: account.balance,
            available_balance: account.available_balance,
            status: account.status,
            created_at: account.created_at,
            activated_at: account.activated_at,
        }
    }
}

// Account summary for listing
#[derive(Debug, Serialize, Deserialize)]
pub struct AccountSummary {
    pub account_id: Uuid,
    pub account_number: String,
    pub account_type: AccountType,
    pub balance: Decimal,
    pub currency: String,
    pub status: AccountStatus,
}
pub mod accounts;
pub mod aft;
pub mod agent_api;
pub mod agent_ledger;
pub mod agents;
pub mod app;
pub mod approvals;
pub mod auth;
pub mod back_office;
pub mod cards;
pub mod customers;
pub mod declines;
pub mod docs;
pub mod finance;
pub mod fraud_admin;
pub mod health;
pub mod interac;
pub mod interac_payees;
pub mod ledger;
pub mod loans;
pub mod lynx;
pub mod mandates;
pub mod ops_levers;
pub mod reviews;
pub mod security;
pub mod transactions;

use std::sync::Arc;

use crate::config::{database::DatabasePool, Settings};
use crate::fraud::FraudCheck;
use crate::ledger::Ledger;

// Shared application state
#[derive(Clone)]
pub struct AppState {
    pub pool: DatabasePool,
    pub settings: Settings,
    /// The accounting core (modern or legacy) behind the swappable Ledger port.
    pub ledger: Arc<dyn Ledger>,
    /// The fraud screening backend (engine or no-op) behind the FraudCheck port.
    pub fraud: Arc<dyn FraudCheck>,
}

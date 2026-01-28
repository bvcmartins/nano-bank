pub mod auth;
pub mod accounts;
pub mod customers;
pub mod docs;
pub mod health;
pub mod security;
pub mod transactions;

use crate::config::{database::DatabasePool, Settings};

// Shared application state
#[derive(Clone)]
pub struct AppState {
    pub pool: DatabasePool,
    pub settings: Settings,
}
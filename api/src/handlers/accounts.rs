use axum::{routing::{get, post}, Router};

use crate::handlers::AppState;

pub fn account_routes() -> Router<AppState> {
    Router::new()
        .route("/", get(get_accounts).post(create_account))
        .route("/:id", get(get_account))
        .route("/:id/balance", get(get_balance))
}

async fn get_accounts() -> &'static str {
    "Get accounts endpoint - TODO: implement"
}

async fn create_account() -> &'static str {
    "Create account endpoint - TODO: implement"
}

async fn get_account() -> &'static str {
    "Get account endpoint - TODO: implement"
}

async fn get_balance() -> &'static str {
    "Get balance endpoint - TODO: implement"
}
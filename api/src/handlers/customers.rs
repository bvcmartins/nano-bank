use axum::{routing::{get, post, put}, Router};

use crate::handlers::AppState;

pub fn customer_routes() -> Router<AppState> {
    Router::new()
        .route("/", post(create_customer))
        .route("/profile", get(get_profile).put(update_profile))
        .route("/kyc/documents", post(upload_kyc_document))
}

async fn create_customer() -> &'static str {
    "Create customer endpoint - TODO: implement"
}

async fn get_profile() -> &'static str {
    "Get profile endpoint - TODO: implement"
}

async fn update_profile() -> &'static str {
    "Update profile endpoint - TODO: implement"
}

async fn upload_kyc_document() -> &'static str {
    "Upload KYC document endpoint - TODO: implement"
}
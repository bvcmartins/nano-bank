use axum::{routing::post, Router};

use crate::handlers::AppState;

pub fn auth_routes() -> Router<AppState> {
    Router::new()
        .route("/login", post(login))
        .route("/refresh", post(refresh_token))
        .route("/logout", post(logout))
}

async fn login() -> &'static str {
    "Login endpoint - TODO: implement"
}

async fn refresh_token() -> &'static str {
    "Refresh token endpoint - TODO: implement"
}

async fn logout() -> &'static str {
    "Logout endpoint - TODO: implement"
}
//! The CTO's audit endpoint. platform_mcp acts on k8s (which the bank cannot
//! see), then posts the attempt here. The actor is PINNED to 'cto' server-side —
//! a caller cannot forge a 'coo'/'cfo' entry — and the append goes through the
//! same hash-chained, immutable `agent_action_ledger` machinery the COO uses.
use axum::{extract::State, routing::post, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::errors::AppError;
use crate::handlers::AppState;
use crate::middleware::auth::AuthenticatedService;

pub fn agent_ledger_routes() -> Router<AppState> {
    Router::new().route("/actions", post(record_action))
}

#[derive(Deserialize)]
struct ActionBody {
    action: String,
    #[serde(default)]
    params: Value,
    #[serde(default)]
    effect: Value,
}

async fn record_action(
    _svc: AuthenticatedService,
    State(state): State<AppState>,
    Json(body): Json<ActionBody>,
) -> Result<Json<Value>, AppError> {
    // Actor is pinned to 'cto' here — never taken from the request.
    let params = if body.params.is_null() {
        json!({})
    } else {
        body.params
    };
    let effect = if body.effect.is_null() {
        json!({})
    } else {
        body.effect
    };
    let (seq, entry_hash): (i64, String) = sqlx::query_as(
        "SELECT seq, entry_hash FROM append_agent_action('cto', $1, $2::jsonb, $3::jsonb)",
    )
    .bind(&body.action)
    .bind(params.to_string())
    .bind(effect.to_string())
    .fetch_one(&state.pool)
    .await?;
    Ok(Json(json!({ "seq": seq, "entry_hash": entry_hash })))
}

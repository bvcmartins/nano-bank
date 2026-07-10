mod aft;
mod config;
mod errors;
mod finance;
mod fraud;
mod handlers;
mod ledger;
mod lynx;
mod middleware;
mod models;
mod outbox;
mod policy;
mod rails;
mod repositories;
mod services;
mod utils;

use axum::{
    extract::DefaultBodyLimit,
    http::{
        header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE},
        HeaderValue, Method,
    },
    routing::get,
    Router,
};
use config::{database::create_connection_pool, Settings};
use std::time::Duration;
use tower::ServiceBuilder;
use tower_http::{
    compression::CompressionLayer,
    cors::{Any, CorsLayer},
    timeout::TimeoutLayer,
    trace::TraceLayer,
};
use tracing::{info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize configuration
    let settings = Settings::new().unwrap_or_else(|err| {
        eprintln!("Failed to load configuration: {}", err);
        eprintln!("Using default configuration");
        Settings::default()
    });

    // Initialize logging
    init_logging(&settings).await;

    info!("🏦 Starting Nano Bank API Server");
    info!("Version: {}", env!("CARGO_PKG_VERSION"));
    info!(
        "Environment: {}",
        std::env::var("RUN_MODE").unwrap_or_else(|_| "development".into())
    );

    // Create database connection pool
    let pool = match create_connection_pool(&settings).await {
        Ok(pool) => {
            info!("✅ Database connection established");
            pool
        }
        Err(e) => {
            warn!("❌ Failed to connect to database: {}", e);
            warn!("💡 Make sure your PostgreSQL cluster is running:");
            warn!("   cd ~/dev/nano-bank && ./k8s/deploy.sh");
            std::process::exit(1);
        }
    };

    // Run database health check
    if let Err(e) = config::database::health_check(&pool).await {
        warn!("❌ Database health check failed: {}", e);
        std::process::exit(1);
    }

    // Verify schema is in place
    if let Err(e) = config::database::run_migrations(&pool).await {
        warn!("❌ Migration check failed: {}", e);
        std::process::exit(1);
    }

    // Ensure the internal GL accounts the card rails post against exist. They
    // are also resolved per-request (idempotent), so this is just early
    // validation — a mid-run data wipe self-heals on the next card operation.
    if let Err(e) = handlers::cards::ensure_system_accounts(&pool).await {
        warn!("❌ Failed to bootstrap system GL accounts: {}", e);
        std::process::exit(1);
    }

    // Bootstrap the Interac rail's clearing/settlement GL accounts (idempotent;
    // also re-resolved per request, so a mid-run wipe self-heals).
    if let Err(e) = rails::interac::ensure_interac_accounts(&pool).await {
        warn!("❌ Failed to bootstrap Interac GL accounts: {}", e);
        std::process::exit(1);
    }

    // Bootstrap the AFT rail's clearing/settlement GL accounts (idempotent).
    if let Err(e) = rails::aft::ensure_aft_accounts(&pool).await {
        warn!("❌ Failed to bootstrap AFT GL accounts: {}", e);
        std::process::exit(1);
    }

    // Bootstrap the Lynx rail's clearing/settlement GL accounts (idempotent).
    if let Err(e) = rails::lynx::ensure_lynx_accounts(&pool).await {
        warn!("❌ Failed to bootstrap Lynx GL accounts: {}", e);
        std::process::exit(1);
    }

    // Create application router
    let app = create_router(pool, &settings).await;

    // Start server
    let listener = tokio::net::TcpListener::bind(&settings.server_address()).await?;

    info!("🚀 Server running on http://{}", settings.server_address());
    info!(
        "📖 API Documentation: http://{}/docs",
        settings.server_address()
    );
    info!(
        "🤝 Agent-consent UI: http://{}/app",
        settings.server_address()
    );
    info!(
        "💚 Health Check: http://{}/health",
        settings.server_address()
    );

    // `into_make_service_with_connect_info` exposes the peer address to handlers
    // via `ConnectInfo<SocketAddr>` — needed to record client IPs on login
    // sessions and failed-login attempts.
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .await?;

    Ok(())
}

async fn create_router(pool: config::database::DatabasePool, settings: &Settings) -> Router {
    // CORS configuration for web frontend
    let cors = CorsLayer::new()
        .allow_origin("http://localhost:3000".parse::<HeaderValue>().unwrap())
        .allow_origin("http://localhost:8080".parse::<HeaderValue>().unwrap())
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::DELETE])
        .allow_credentials(true)
        .allow_headers([AUTHORIZATION, ACCEPT, CONTENT_TYPE]);

    // Select the accounting core behind the swappable Ledger port.
    let ledger = build_ledger();
    let fraud = build_fraud_check(settings);
    info!("🔌 Ledger backend: {}", ledger.backend());

    // Create application state
    let app_state = handlers::AppState {
        pool: pool.clone(),
        settings: settings.clone(),
        ledger,
        fraud,
    };

    // Build the router
    Router::new()
        // Health check endpoint
        .route("/health", get(handlers::health::health_check))
        // API documentation
        .route("/docs", get(handlers::docs::api_docs))
        // Built-in consent UI (agent registration, mandates, activity)
        .route("/app", get(handlers::app::consent_app))
        // Authentication routes
        .nest("/api/v1/auth", handlers::auth::auth_routes())
        // Customer routes
        .nest(
            "/api/v1/customers",
            handlers::customers::customer_routes()
                .merge(handlers::interac_payees::recipient_routes()),
        )
        // Account routes
        .nest("/api/v1/accounts", handlers::accounts::account_routes())
        // Agentic banking: agent registration/metadata, consent mandates, and
        // the mandate-scoped agent surface (no account params — the mandate
        // pins the account)
        .nest("/api/v1/agents", handlers::agents::agent_routes())
        .nest("/api/v1/mandates", handlers::mandates::mandate_routes())
        .nest("/api/v1/agent", handlers::agent_api::agent_api_routes())
        // Step-up approvals (Phase 3): the customer resolves parked transfers
        .nest("/api/v1/approvals", handlers::approvals::approval_routes())
        // Fraud reviews: the customer follows a movement the engine held. The
        // neighbouring plane to approvals, and deliberately separate — there a
        // principal grants permission, here a reviewer adjudicates risk.
        .nest("/api/v1/reviews", handlers::reviews::review_routes())
        // Back-office read plane: service-token reads *across* customers, for a
        // CRM or support console. Read-only by design — see the module docs.
        .nest(
            "/api/v1/back-office",
            handlers::back_office::back_office_routes(),
        )
        // Credit-card payment rails (issuer endpoints)
        .nest("/api/v1/cards", handlers::cards::card_routes())
        // Interest / NIM engine (spec #2): daily accrual + monthly capitalisation
        .nest("/api/v1/finance", handlers::finance::finance_routes())
        .nest("/api/v1/fraud", handlers::fraud_admin::fraud_admin_routes())
        // Interac e-Transfer rails
        .nest("/api/v1/interac", handlers::interac::interac_routes())
        .nest("/api/v1/aft", handlers::aft::aft_routes())
        .nest("/api/v1/lynx", handlers::lynx::lynx_routes())
        .nest(
            "/api/v1/ops-levers",
            handlers::ops_levers::ops_lever_routes(),
        )
        .nest(
            "/api/v1/agent-ledger",
            handlers::agent_ledger::agent_ledger_routes(),
        )
        // Transaction routes
        .nest(
            "/api/v1/transactions",
            handlers::transactions::transaction_routes(),
        )
        // General-ledger journal posting through the swappable Ledger port
        .nest("/api/v1/ledger", handlers::ledger::ledger_routes())
        // Lending and loan subsystem
        .nest("/api/v1/loans", handlers::loans::loan_routes())
        // Security routes
        .nest("/api/v1/security", handlers::security::security_routes())
        // Add middleware layers
        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
                .layer(CompressionLayer::new())
                .layer(TimeoutLayer::new(Duration::from_secs(30)))
                .layer(axum::middleware::from_fn(fraud::simulated_time::capture))
                .layer(DefaultBodyLimit::max(10 * 1024 * 1024)) // 10MB max request size
                .layer(cors),
        )
        .with_state(app_state)
}

/// Construct the fraud screening backend selected by `[fraud] backend`
/// (`NANO_BANK__FRAUD__BACKEND`): "engine" = the HTTP fraud engine, anything
/// else = no-op (default off — zero behavior change until opted in).
fn build_fraud_check(settings: &Settings) -> std::sync::Arc<dyn fraud::FraudCheck> {
    use std::sync::Arc;
    match settings.fraud.backend.as_str() {
        "engine" => {
            info!(url = %settings.fraud.engine_url, "fraud screening: engine backend");
            Arc::new(fraud::engine::EngineFraudCheck::new(
                settings.fraud.engine_url.clone(),
                settings.fraud.service_token.clone(),
                settings.fraud.timeout_ms,
                settings.fraud.outcomes_timeout_ms,
            ))
        }
        other => {
            if other != "off" {
                warn!(backend = other, "unknown fraud backend, screening is OFF");
            }
            Arc::new(fraud::noop::NoopFraudCheck)
        }
    }
}

/// Construct the accounting core selected by `CORE_BACKEND` (modern | legacy).
/// Both are HTTP peers; the URLs default to their local ports.
fn build_ledger() -> std::sync::Arc<dyn ledger::Ledger> {
    use std::sync::Arc;
    match std::env::var("CORE_BACKEND").as_deref() {
        Ok("legacy") => {
            let url =
                std::env::var("LEGACY_CORE_URL").unwrap_or_else(|_| "http://localhost:8090".into());
            Arc::new(ledger::legacy::LegacyLedger::new(url))
        }
        _ => {
            let url =
                std::env::var("MODERN_CORE_URL").unwrap_or_else(|_| "http://localhost:8091".into());
            Arc::new(ledger::modern::ModernLedger::new(url))
        }
    }
}

async fn init_logging(settings: &Settings) {
    // Create a custom subscriber based on configuration
    let subscriber = tracing_subscriber::registry();

    let fmt_layer = tracing_subscriber::fmt::layer()
        .compact()
        .with_target(false)
        .with_thread_ids(true)
        .with_line_number(true);

    subscriber
        .with(fmt_layer)
        .with(tracing_subscriber::EnvFilter::new(&settings.logging.level))
        .init();

    info!(
        "🔍 Logging initialized - level: {}, format: {}",
        settings.logging.level, settings.logging.format
    );
}

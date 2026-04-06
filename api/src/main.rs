mod config;
mod errors;
mod handlers;
mod middleware;
mod models;
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
    info!("Environment: {}", std::env::var("RUN_MODE").unwrap_or_else(|_| "development".into()));

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

    // Create application router
    let app = create_router(pool, &settings).await;

    // Start server
    let listener = tokio::net::TcpListener::bind(&settings.server_address()).await?;

    info!("🚀 Server running on http://{}", settings.server_address());
    info!("📖 API Documentation: http://{}/docs", settings.server_address());
    info!("💚 Health Check: http://{}/health", settings.server_address());

    axum::serve(listener, app).await?;

    Ok(())
}

async fn create_router(
    pool: config::database::DatabasePool,
    settings: &Settings,
) -> Router {
    // CORS configuration for web frontend
    let cors = CorsLayer::new()
        .allow_origin("http://localhost:3000".parse::<HeaderValue>().unwrap())
        .allow_origin("http://localhost:8080".parse::<HeaderValue>().unwrap())
        .allow_methods([Method::GET, Method::POST, Method::PUT, Method::DELETE])
        .allow_credentials(true)
        .allow_headers([AUTHORIZATION, ACCEPT, CONTENT_TYPE]);

    // Create application state
    let app_state = handlers::AppState {
        pool: pool.clone(),
        settings: settings.clone(),
    };

    // Build the router
    Router::new()
        // Health check endpoint
        .route("/health", get(handlers::health::health_check))

        // API documentation
        .route("/docs", get(handlers::docs::api_docs))

        // Authentication routes
        .nest("/api/v1/auth", handlers::auth::auth_routes())

        // Customer routes
        .nest("/api/v1/customers", handlers::customers::customer_routes())

        // Account routes
        .nest("/api/v1/accounts", handlers::accounts::account_routes())

        // Transaction routes
        .nest("/api/v1/transactions", handlers::transactions::transaction_routes())

        // Security routes
        .nest("/api/v1/security", handlers::security::security_routes())

        // Add middleware layers
        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
                .layer(CompressionLayer::new())
                .layer(TimeoutLayer::new(Duration::from_secs(30)))
                .layer(DefaultBodyLimit::max(10 * 1024 * 1024)) // 10MB max request size
                .layer(cors)
        )
        .with_state(app_state)
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

    info!("🔍 Logging initialized - level: {}, format: {}",
          settings.logging.level, settings.logging.format);
}

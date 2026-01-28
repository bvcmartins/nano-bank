use crate::config::Settings;
use sqlx::postgres::{PgPool, PgPoolOptions};
use sqlx::Error as SqlxError;
use std::time::Duration;
use tracing::{info, warn};

pub type DatabasePool = PgPool;

pub async fn create_connection_pool(settings: &Settings) -> Result<DatabasePool, SqlxError> {
    info!("Creating database connection pool...");

    let database_url = settings.database_url();

    let pool = PgPoolOptions::new()
        .max_connections(settings.database.max_connections)
        .min_connections(settings.database.min_connections)
        .acquire_timeout(Duration::from_secs(settings.database.acquire_timeout))
        .idle_timeout(Duration::from_secs(600)) // 10 minutes
        .max_lifetime(Duration::from_secs(1800)) // 30 minutes
        .connect(&database_url)
        .await?;

    // Test the connection
    match sqlx::query("SELECT 1").fetch_one(&pool).await {
        Ok(_) => {
            info!("Database connection pool created successfully");
            info!("Connected to database: {}", settings.database.database_name);
        }
        Err(e) => {
            warn!("Failed to test database connection: {}", e);
            return Err(e);
        }
    }

    Ok(pool)
}

pub async fn run_migrations(pool: &DatabasePool) -> Result<(), sqlx::Error> {
    info!("Running database migrations...");

    // Note: In a real application, you would run actual migrations here
    // For now, we'll just verify that the tables exist
    let table_check = sqlx::query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'customers'")
        .fetch_optional(pool)
        .await?;

    match table_check {
        Some(_) => {
            info!("Database tables found - migrations appear to be complete");
        }
        None => {
            warn!("Database tables not found - please run the SQL scripts manually");
            warn!("Run the scripts in ~/dev/nano-bank/src/core/tables/ in order");
        }
    }

    Ok(())
}

pub async fn health_check(pool: &DatabasePool) -> Result<(), SqlxError> {
    sqlx::query("SELECT 1").fetch_one(pool).await?;
    Ok(())
}
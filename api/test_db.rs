use sqlx::postgres::PgPoolOptions;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let database_url = "postgresql://nanobank_user:secure_nano_password_2024!@127.0.0.1:5432/nano_bank_db?sslmode=disable";
    
    println!("Connecting to: {}", database_url.replace("secure_nano_password_2024!", "***"));
    
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&database_url)
        .await?;
    
    let result: (i32,) = sqlx::query_as("SELECT 1")
        .fetch_one(&pool)
        .await?;
    
    println!("Success! Result: {}", result.0);
    
    Ok(())
}

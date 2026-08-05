//! Concurrency regression test for the shared outbox claim primitive
//! (`api/src/outbox.rs::OutboxClaim`) — issue #32 item 1.
//!
//! The production drainers drain GLOBALLY and `cargo test` runs tests in
//! parallel, so a claim raced against a real outbox table would contend with
//! every other test. Instead this drives the claim against an EPHEMERAL table
//! (unique name per run) that nothing else can touch. Because `OutboxClaim` is
//! table-name-parameterized, the SQL under test is the genuine production
//! template — only the table name differs. Skips (still passes) when the test
//! Postgres is unreachable, same harness as `tests/agents.rs`.

#[path = "../src/outbox.rs"]
mod outbox;

use outbox::OutboxClaim;
use sqlx::Row;
use std::collections::HashSet;
use uuid::Uuid;

/// The attempts budget bound into the claim's `$1` (mirrors the drainers'
/// MAX_DELIVERY_ATTEMPTS; the exact value only has to exceed the seeded 0s).
const MAX_ATTEMPTS: i32 = 5;

/// Connect to the test Postgres, or `None` (with a SKIP note) if unreachable —
/// the offline-skip contract every integration test here honours.
async fn test_db() -> Option<sqlx::PgPool> {
    let url = std::env::var("NANO_BANK_TEST_DB_URL").unwrap_or_else(|_| {
        "postgres://nanobank_user:secure_nano_password_2024!@[::1]:5432/nano_bank_db".to_string()
    });
    match sqlx::PgPool::connect(&url).await {
        Ok(pool) => Some(pool),
        Err(e) => {
            eprintln!("SKIP: DB unreachable ({e})");
            None
        }
    }
}

/// Create an ephemeral table with the exact bookkeeping shape the claim touches
/// (`delivered` / `delivery_attempts` / `created_at` / UUID PK `id`). The uuid
/// suffix makes the name unique per run — isolated from parallel tests and from
/// any leftover of a crashed prior run. Returns the table name.
async fn make_table(pool: &sqlx::PgPool) -> String {
    let name = format!("outbox_claim_test_{}", Uuid::new_v4().simple());
    sqlx::query(&format!(
        "CREATE TABLE {name} ( \
             id UUID PRIMARY KEY DEFAULT gen_random_uuid(), \
             delivered BOOLEAN NOT NULL DEFAULT FALSE, \
             delivery_attempts INTEGER NOT NULL DEFAULT 0, \
             created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP )"
    ))
    .execute(pool)
    .await
    .expect("create ephemeral table");
    name
}

/// Best-effort teardown; ignores errors so it never masks a real assertion.
async fn drop_table(pool: &sqlx::PgPool, name: &str) {
    let _ = sqlx::query(&format!("DROP TABLE IF EXISTS {name}"))
        .execute(pool)
        .await;
}

/// Seed `n` undelivered rows (attempts = 0) with strictly increasing
/// `created_at`, so the claim's `ORDER BY created_at` is deterministic.
async fn seed(pool: &sqlx::PgPool, table: &str, n: i32) {
    sqlx::query(&format!(
        "INSERT INTO {table} (created_at) \
         SELECT CURRENT_TIMESTAMP + (g || ' microseconds')::interval \
         FROM generate_series(1, $1) g"
    ))
    .bind(n)
    .execute(pool)
    .await
    .expect("seed rows");
}

/// Run one claim inside an already-open transaction and return the claimed ids.
/// Takes the transaction by `&mut` so the caller controls when it commits —
/// that's what lets us hold one claimer's locks while another claims.
async fn claim(tx: &mut sqlx::Transaction<'_, sqlx::Postgres>, sql: &str, batch: i64) -> Vec<Uuid> {
    sqlx::query(sql)
        .bind(MAX_ATTEMPTS)
        .bind(batch)
        .fetch_all(&mut **tx)
        .await
        .expect("claim")
        .into_iter()
        .map(|r| r.get::<Uuid, _>("id"))
        .collect()
}

#[tokio::test]
async fn concurrent_claims_are_exactly_once() {
    let Some(pool) = test_db().await else { return };
    let table = make_table(&pool).await;

    const K: i32 = 50;
    // Batch > K/2 so the two claims' candidate windows overlap: the second
    // claimer WANTS rows the first has locked, so it can only stay disjoint by
    // skipping them. K - BATCH < BATCH, so the remainder (20) is deterministic.
    const BATCH: i64 = 30;
    seed(&pool, &table, K).await;

    let sql = OutboxClaim {
        table: &table,
        id_column: "id",
        returning: "id",
    }
    .sql();

    // Deterministic contention (more reliable than racing the scheduler): open
    // transaction A and claim — locking its rows but NOT committing — then claim
    // in transaction B while A still holds those locks. That is precisely the
    // condition FOR UPDATE SKIP LOCKED exists for: B must skip A's locked rows.
    // Drop SKIP LOCKED and B instead BLOCKS on A's locks here (deadlock/hang),
    // or, if it could proceed, re-claims A's still-eligible rows (attempts -> 2).
    let mut tx_a = pool.begin().await.expect("begin a");
    let a = claim(&mut tx_a, &sql, BATCH).await;

    let mut tx_b = pool.begin().await.expect("begin b");
    let b = claim(&mut tx_b, &sql, BATCH).await;

    tx_a.commit().await.expect("commit a");
    tx_b.commit().await.expect("commit b");

    let set_a: HashSet<Uuid> = a.iter().copied().collect();
    let set_b: HashSet<Uuid> = b.iter().copied().collect();

    // The core property: no row is claimed by both claimers.
    assert!(
        set_a.is_disjoint(&set_b),
        "a row was claimed by both: {:?}",
        set_a.intersection(&set_b).collect::<Vec<_>>()
    );
    // No duplicate ids within a single claim.
    assert_eq!(set_a.len(), a.len(), "duplicate ids within claim A");
    assert_eq!(set_b.len(), b.len(), "duplicate ids within claim B");
    // A takes its full batch; B skips those locked rows and gets the remainder.
    assert_eq!(a.len(), BATCH as usize, "first claim takes its full batch");
    assert_eq!(
        b.len(),
        (K as i64 - BATCH) as usize,
        "second claim skips locked rows, takes only the remainder"
    );

    // Row-level ledger: every claimed row incremented EXACTLY once (0 -> 1),
    // none twice, and no rows left unclaimed (A+B cover all K).
    let claimed_once: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts = 1"
    ))
    .fetch_one(&pool)
    .await
    .unwrap();
    let untouched: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts = 0"
    ))
    .fetch_one(&pool)
    .await
    .unwrap();
    let double: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts > 1"
    ))
    .fetch_one(&pool)
    .await
    .unwrap();

    assert_eq!(double, 0, "a row was incremented more than once");
    assert_eq!(claimed_once, K as i64, "every seeded row claimed exactly once");
    assert_eq!(untouched, 0, "no row left unclaimed");

    drop_table(&pool, &table).await;
}

#[tokio::test]
async fn claim_skips_rows_past_the_attempt_budget() {
    let Some(pool) = test_db().await else { return };
    let table = make_table(&pool).await;

    // One under-budget row and one already at the cap (a dead letter).
    sqlx::query(&format!(
        "INSERT INTO {table} (delivery_attempts) VALUES (0), ($1)"
    ))
    .bind(MAX_ATTEMPTS)
    .execute(&pool)
    .await
    .expect("seed budget rows");

    let sql = OutboxClaim {
        table: &table,
        id_column: "id",
        returning: "id, delivery_attempts",
    }
    .sql();
    let rows = sqlx::query(&sql)
        .bind(MAX_ATTEMPTS)
        .bind(10i64)
        .fetch_all(&pool)
        .await
        .expect("claim");

    // The `delivery_attempts < $1` predicate leaves the capped row behind.
    assert_eq!(rows.len(), 1, "capped row must not be claimed");
    let attempts: i32 = rows[0].get("delivery_attempts");
    assert_eq!(attempts, 1, "claimed row incremented 0 -> 1");

    drop_table(&pool, &table).await;
}

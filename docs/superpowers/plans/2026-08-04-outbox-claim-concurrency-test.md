# Outbox Claim Concurrency Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a behavioural regression test proving the shared outbox claim primitive (`OutboxClaim`) grants each row to at most one concurrent claimer (exactly-once), closing issue #32 item 1.

**Architecture:** `OutboxClaim` is table-name-parameterized by design, and both production outbox tables share the identical bookkeeping shape (`delivered`, `delivery_attempts`, `created_at`, a UUID PK). A new integration test creates an *ephemeral* table of that shape (unique name per run, so it's isolated from every other parallel test and from the global-draining production drainers), seeds rows, and races two `OutboxClaim.sql()` claims in separate transactions against it. The only production edit is loosening the struct's field lifetimes from `&'static str` to `&'a str` so the real template can render a runtime table name — a behaviourally inert type-signature change.

**Tech Stack:** Rust, `sqlx` 0.7 (Postgres), `tokio`, `uuid`. Test lives under `api/tests/`.

> **Implementation note (deviation from Task 2 as first drafted):** the original
> Task 2 raced two claims with `tokio::join!`. On the current-thread test runtime
> the two futures ran effectively serially — claim B ran after A had committed and
> re-claimed the same still-eligible rows (all identical), a false failure. The
> shipped test instead uses **deterministic contention**: open transaction A and
> claim (locking rows, *uncommitted*), then claim in transaction B while A holds
> those locks — the exact condition `FOR UPDATE SKIP LOCKED` exists for. A takes
> its full batch (30), B skips the locked rows and takes the remainder (20),
> disjoint. Verified to *hang* (deadlock, caught by `timeout`) when `SKIP LOCKED`
> is removed. The code blocks below reflect the original draft; the committed
> `api/tests/outbox.rs` is the deterministic version.

## Global Constraints

- **DB host is `::1`, not `127.0.0.1`** (dead docker-proxy on IPv4). The test DB URL default is `postgres://nanobank_user:secure_nano_password_2024!@[::1]:5432/nano_bank_db`, overridable via `NANO_BANK_TEST_DB_URL`.
- **Skip-if-no-DB harness:** every integration test here probes its dependency and `eprintln!("SKIP: ...")` + `return`s (still passes) when unreachable, so `cargo test` is green with nothing running. This new file follows that exactly — no DB, no failure.
- **No behavioural production change.** The single production edit (Task 1) is a lifetime loosening with zero runtime effect; the existing two call sites and the existing unit test must keep compiling unchanged.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- All `cargo`/`git` commands run from `api/` unless noted.

---

### Task 1: Loosen `OutboxClaim` field lifetimes to accept a runtime table name

**Files:**
- Modify: `api/src/outbox.rs:20-28` (struct def) and `api/src/outbox.rs:30-31` (impl header)

**Interfaces:**
- Consumes: nothing.
- Produces: `pub struct OutboxClaim<'a> { pub table: &'a str, pub id_column: &'a str, pub returning: &'a str }` with `impl<'a> OutboxClaim<'a> { pub fn sql(&self) -> String }`. Both existing call sites (`handlers/fraud_admin.rs:92`, `handlers/interac.rs:376`) pass `&'static str` literals, which coerce to `&'a str` with the lifetime inferred — no call-site edits needed.

- [ ] **Step 1: Apply the lifetime loosening**

In `api/src/outbox.rs`, change the struct from:

```rust
pub struct OutboxClaim {
    /// The outbox table.
    pub table: &'static str,
    /// Its primary key, used both to lock and to return.
    pub id_column: &'static str,
    /// The `RETURNING` projection: the id column plus whatever the drainer
    /// needs to deliver the row.
    pub returning: &'static str,
}
```

to (only the three types change, `'static` → `'a`, plus the generic param):

```rust
pub struct OutboxClaim<'a> {
    /// The outbox table.
    pub table: &'a str,
    /// Its primary key, used both to lock and to return.
    pub id_column: &'a str,
    /// The `RETURNING` projection: the id column plus whatever the drainer
    /// needs to deliver the row.
    pub returning: &'a str,
}
```

And change the impl header from `impl OutboxClaim {` to:

```rust
impl<'a> OutboxClaim<'a> {
```

Leave the body of `sql()` and the `#[cfg(test)] mod tests` block untouched.

- [ ] **Step 2: Verify the crate still type-checks with call sites unchanged**

Run: `cargo check`
Expected: PASS with no new errors. (The two `OutboxClaim { table: "…", … }` literals in `handlers/fraud_admin.rs` and `handlers/interac.rs` infer `OutboxClaim<'static>` and need no edit.)

- [ ] **Step 3: Verify the existing unit test still passes**

Run: `cargo test --lib outbox::tests::claim_keeps_the_concurrency_clauses`
Expected: PASS (1 test). This proves the loosening is behaviourally inert.

- [ ] **Step 4: Commit**

```bash
git add api/src/outbox.rs
git commit -m "refactor(outbox): loosen OutboxClaim field lifetimes to &'a str

Lets a caller render the claim template against a runtime table name.
Behaviourally inert: the two &'static str call sites coerce unchanged.
Enables the concurrency regression test (issue #32 item 1).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Concurrency exactly-once regression test

**Files:**
- Create: `api/tests/outbox.rs`

**Interfaces:**
- Consumes: `OutboxClaim<'a>` from Task 1 (included into the test binary via `#[path = "../src/outbox.rs"] mod outbox;` — `outbox.rs` is a self-contained leaf module with no crate-internal imports, so it compiles standalone; this keeps the change test-only rather than requiring a `lib.rs`). Note: the included file's own `#[cfg(test)] mod tests` also compiles and runs in this test binary — a harmless duplicate run of `claim_keeps_the_concurrency_clauses`.
- Produces: helpers `test_db()`, `make_table()`, `drop_table()`, `seed()` used by Task 3 in the same file.

- [ ] **Step 1: Write the failing test (whole file, harness + first test)**

Create `api/tests/outbox.rs`:

```rust
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

#[tokio::test]
async fn concurrent_claims_are_exactly_once() {
    let Some(pool) = test_db().await else { return };
    let table = make_table(&pool).await;

    const K: i32 = 50;
    // Batch > K/2 so the two claims' candidate windows MUST overlap: if SKIP
    // LOCKED were dropped, they'd contend for the same rows and double-claim.
    const BATCH: i64 = 30;
    seed(&pool, &table, K).await;

    let sql = OutboxClaim { table: &table, id_column: "id", returning: "id" }.sql();

    // Two independent transactions (two pooled connections) racing the same
    // candidate window. Each claims, then commits.
    let claim = |sql: String, pool: sqlx::PgPool| async move {
        let mut tx = pool.begin().await.expect("begin");
        let rows = sqlx::query(&sql)
            .bind(MAX_ATTEMPTS)
            .bind(BATCH)
            .fetch_all(&mut *tx)
            .await
            .expect("claim");
        tx.commit().await.expect("commit");
        rows.into_iter().map(|r| r.get::<Uuid, _>("id")).collect::<Vec<_>>()
    };

    let (a, b) = tokio::join!(
        claim(sql.clone(), pool.clone()),
        claim(sql.clone(), pool.clone()),
    );

    let set_a: HashSet<Uuid> = a.iter().copied().collect();
    let set_b: HashSet<Uuid> = b.iter().copied().collect();

    // The core property: no row is claimed by both racers.
    assert!(
        set_a.is_disjoint(&set_b),
        "a row was claimed by both: {:?}",
        set_a.intersection(&set_b).collect::<Vec<_>>()
    );
    // No duplicate ids within a single claim.
    assert_eq!(set_a.len(), a.len(), "duplicate ids within claim A");
    assert_eq!(set_b.len(), b.len(), "duplicate ids within claim B");
    // Each batch is bounded by LIMIT, and together they never exceed the seed.
    assert!(a.len() as i64 <= BATCH, "claim A over batch");
    assert!(b.len() as i64 <= BATCH, "claim B over batch");
    assert!((set_a.len() + set_b.len()) as i32 <= K, "claimed more than seeded");

    // Row-level ledger: every claimed row incremented EXACTLY once (0 -> 1),
    // none twice, and the unclaimed remainder untouched. If SKIP LOCKED were
    // removed, the loser would re-lock the winner's still-eligible rows and
    // push some to attempts = 2 — caught here.
    let claimed_once: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts = 1"
    )).fetch_one(&pool).await.unwrap();
    let untouched: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts = 0"
    )).fetch_one(&pool).await.unwrap();
    let double: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM {table} WHERE delivery_attempts > 1"
    )).fetch_one(&pool).await.unwrap();

    assert_eq!(double, 0, "a row was incremented more than once");
    assert_eq!(
        claimed_once as usize,
        set_a.len() + set_b.len(),
        "increment count must equal the number of claimed rows"
    );
    assert_eq!((claimed_once + untouched) as i32, K, "row accounting");

    drop_table(&pool, &table).await;
}
```

- [ ] **Step 2: Run it and confirm it passes against a live DB (or SKIPs offline)**

Bring the Kind Postgres up if it isn't (`./k8s/deploy.sh` from repo root, then `kubectl port-forward -n nano-bank svc/postgres-service 5432:5432`), then run:

Run: `cargo test --test outbox concurrent_claims_are_exactly_once -- --nocapture`
Expected: PASS with a live DB. With no DB reachable it prints `SKIP: DB unreachable (...)` and still PASSES — confirm one of these two outcomes, never a hard failure.

- [ ] **Step 3: Prove the test actually bites (temporary mutation)**

Temporarily edit `api/src/outbox.rs` `sql()` to delete ` FOR UPDATE SKIP LOCKED` (leave a bare subquery). Re-run against a live DB:

Run: `cargo test --test outbox concurrent_claims_are_exactly_once -- --nocapture`
Expected: FAIL — either the `double == 0` assertion trips (a row hit attempts = 2) or the disjointness assertion trips. If it still PASSES, the racers aren't contending; raise `K` to 200 / `BATCH` to 150 and retry. Then **revert** the mutation to `outbox.rs` and confirm `cargo test --test outbox` is green again.

- [ ] **Step 4: Commit**

```bash
git add api/tests/outbox.rs
git commit -m "test(outbox): concurrency regression for exactly-once claim

Races two OutboxClaim.sql() claims against an ephemeral outbox-shaped
table and asserts disjoint claims + per-row attempts==1. Verified to fail
when FOR UPDATE SKIP LOCKED is removed. Issue #32 item 1.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Attempt-budget (dead-letter) boundary test

**Files:**
- Modify: `api/tests/outbox.rs` (append one `#[tokio::test]`)

**Interfaces:**
- Consumes: `test_db()`, `make_table()`, `drop_table()`, `MAX_ATTEMPTS` from Task 2; `OutboxClaim` from Task 1.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Append to `api/tests/outbox.rs`:

```rust
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

    let sql = OutboxClaim { table: &table, id_column: "id", returning: "id, delivery_attempts" }.sql();
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
```

- [ ] **Step 2: Run it**

Run: `cargo test --test outbox claim_skips_rows_past_the_attempt_budget -- --nocapture`
Expected: PASS against a live DB, or `SKIP` + PASS offline.

- [ ] **Step 3: Run the whole test file once**

Run: `cargo test --test outbox -- --nocapture`
Expected: both tests PASS (or SKIP). No warnings from the new file.

- [ ] **Step 4: Commit**

```bash
git add api/tests/outbox.rs
git commit -m "test(outbox): claim skips rows past the attempt budget

Guards the dead-letter edge (delivery_attempts < \$1) behaviourally.
Issue #32 item 1.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Post-plan (not code steps)

- Open the PR from the working branch; the description references issue #32 item 1 and notes the one production touch (the lifetime loosening) is behaviourally inert.
- After the PR is up, close issue #32 with a comment pointing to it (the user's step 2). Item 2 was already delivered by the `OutboxClaim` extraction (#39); this PR delivers item 1.

## Self-Review

**Spec coverage:**
- Approach A (ephemeral test-owned table, genuine template via table-name param) — Task 2 `make_table` + `OutboxClaim{table:&name,...}`. ✔
- Skip-if-no-DB harness — `test_db()` in Task 2. ✔
- `&'static str` → `&'a str` loosening (spec's option 2, the one production touch) — Task 1, verified inert in Steps 2–3. ✔
- Core exactly-once assertion (disjoint + covers, per-row attempts) — Task 2 test. ✔
- Budget / dead-letter boundary assertion — Task 3 test. ✔
- Existing string unit test retained — untouched in Task 1; noted duplicate-run via `#[path]` include. ✔
- Deliverable = one PR (new test file + loosening), then close #32 — Post-plan section. ✔

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". All code blocks are complete and runnable. ✔

**Type consistency:** `OutboxClaim<'a>` fields `table`/`id_column`/`returning` (Task 1) match the literal keys used in Tasks 2–3. Helpers `test_db`/`make_table`/`drop_table`/`seed` and const `MAX_ATTEMPTS` defined in Task 2, consumed by Task 3 with matching signatures. `sql()` returns `String`; `.bind(MAX_ATTEMPTS: i32).bind(BATCH: i64)` matches the claim's `$1`/`$2`. ✔

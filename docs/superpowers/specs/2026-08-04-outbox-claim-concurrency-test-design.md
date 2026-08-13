# Outbox claim: concurrency regression test

**Issue:** #32 item 1 — make the outbox drainer's exactly-once claim property
directly testable under concurrency.
**Date:** 2026-08-04
**Scope:** test-only. No production code changes.

## Problem

`api/src/outbox.rs::OutboxClaim` is the single claim primitive shared by every
outbox drainer (`handlers/fraud_admin.rs::flush_denials`,
`handlers/interac.rs::flush_notifications`). Its exactly-once-under-concurrency
guarantee lives entirely in one SQL string:

```sql
UPDATE {table} SET delivery_attempts = delivery_attempts + 1
WHERE {id} IN (
    SELECT {id} FROM {table}
    WHERE delivered = FALSE AND delivery_attempts < $1
    ORDER BY created_at LIMIT $2 FOR UPDATE SKIP LOCKED )
RETURNING {returning}
```

The only existing test (`claim_keeps_the_concurrency_clauses`) asserts that the
clause **substrings** are present. It cannot observe the behaviour those clauses
exist for: two concurrent claimers must never grab the same row.

Why a naive test is hard: both production drainers drain **globally** (no scope
predicate), and `cargo test` runs tests in parallel. A test that seeds rows into
a real outbox table and fires concurrent claims would race every other test
touching that table. Adding a scope filter to the production claim purely to
isolate the test would be a test-only smell (issue #32's own caveat).

## Chosen approach (A): test the claim SQL against a test-owned table

`OutboxClaim` is **table-name-parameterized by design** — that parameterization
is its whole abstraction. Both real outbox tables share the identical bookkeeping
columns the claim touches:

| column              | type                     |
|---------------------|--------------------------|
| `{id}` (PK)         | UUID                     |
| `delivered`         | BOOLEAN NOT NULL         |
| `delivery_attempts` | INTEGER NOT NULL         |
| `created_at`        | TIMESTAMPTZ NOT NULL     |

So the test creates an **ephemeral table of that shape** (unique name per run),
seeds K undelivered rows, and drives `OutboxClaim.sql()` against it — exercising
the *actual* production SQL (identical but for the table name) under real
Postgres concurrency, on a table nothing else can touch.

This isolates the test **without** adding any production scope filter — it
resolves issue #32's "test-only filter is a smell" concern by removing the need
for a filter, not working around it.

## Test file

New `api/tests/outbox.rs`, following the existing skip-if-no-DB harness
(`tests/agents.rs`): connect to the test Postgres via `NANO_BANK_TEST_DB_URL`
(default `postgres://nanobank_user:...@[::1]:5432/nano_bank_db`); on connect
failure print `SKIP:` and return (still passes), so it's a no-op in CI without a
live DB, same as every other integration test here.

### Fixture: ephemeral outbox table

- Name: `outbox_claim_test_<uuid-hex>` (unique per run → parallel-safe, and the
  uuid also dodges any leftover table from a crashed prior run).
- Columns exactly as the shared shape above; `id` PK defaults to
  `gen_random_uuid()`, `created_at` defaults to `CURRENT_TIMESTAMP`.
- Wrapped so the table is **dropped at the end** (`DROP TABLE IF EXISTS`),
  including on assertion failure — a small guard/helper, not a `Drop` impl (async
  drop isn't available); simplest is to run the body and drop before the final
  asserts, or drop in both success and the panic path. Decide in the plan.

### The claim under test

`OutboxClaim { table: &fixture_name, id_column: "id", returning: "id" }.sql()`.
Note `table`/`id_column`/`returning` are `&'static str` in production (they're
compile-time literals there); the test needs a runtime table name. Two options
for the plan to weigh:

1. Build the same SQL string in the test from the fixture name (duplicates the
   template — drifts from production).
2. Loosen `OutboxClaim`'s fields from `&'static str` to `&'a str` (or `String`)
   so the *real* `.sql()` renders the test's table. **Preferred** — the test then
   exercises the genuine template, and the lifetime change is inert for the
   existing `&'static` callers. This is the one arguable production touch; it is a
   type-signature loosening with no behavioural effect, justified because it lets
   the primitive be tested against its own contract. Flag it explicitly in the PR.

The plan should confirm option 2 compiles with the existing two call sites
unchanged (a `&'static str` coerces to `&'a str`), keeping the "no behavioural
production change" property.

### Core assertion — exactly-once under concurrency

1. Seed K = 50 rows (`delivered=FALSE`, `delivery_attempts=0`).
2. Open **two** separate DB transactions (two connections from the pool).
3. In each, run the claim with `$1 = MAX` (attempts budget, e.g. 5) and
   `$2 = 30` (batch > half of K, so the two batches *must* contend for overlap if
   SKIP LOCKED were broken).
4. Run them **concurrently** (`tokio::join!`), each committing its own tx.
5. Collect the two returned id sets. Assert:
   - **Disjoint:** `set_a ∩ set_b = ∅` (no row claimed twice — the core property).
   - **Bounded:** `|set_a| + |set_b| ≤ K` and each `≤ 30`.
   - **Increment:** every claimed row now has `delivery_attempts = 1` (claimed
     exactly once, not twice).
   - Unclaimed rows (K − total) remain `delivery_attempts = 0`.

Two concurrent `FOR UPDATE SKIP LOCKED` transactions against overlapping
candidate windows are the exact production race; without SKIP LOCKED one would
block on the other (or, with a plain read, both could grab the same ids). The
disjointness + per-row `delivery_attempts = 1` assertions fail loudly if the
concurrency clauses are ever dropped from the template.

### Second assertion — budget / dead-letter boundary (cheap add)

Seed a row at `delivery_attempts = MAX`; assert a claim does **not** return it
(the `delivery_attempts < $1` predicate). Guards the dead-letter edge that the
string test also only checks textually.

## Out of scope

- The HTTP admin drainers, delivery seam, retention/purge — unchanged.
- The existing `claim_keeps_the_concurrency_clauses` unit test stays (cheap,
  guards the string shape); this new file adds the behavioural coverage.

## Deliverable

One PR: `api/tests/outbox.rs` (new) + the `&'static str` → `&'a str` loosening in
`api/src/outbox.rs` (if the plan confirms option 2). Then close issue #32 with a
pointer to the PR.

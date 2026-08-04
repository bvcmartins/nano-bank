//! The transactional-outbox claim, shared by every drainer.
//!
//! The bank has two outboxes — Interac notifications and agent denials — and
//! will grow more. They differ in what they carry and who they deliver to, but
//! the act of *claiming* work is identical, and getting it wrong is expensive
//! in ways that don't show up in testing: drop `FOR UPDATE SKIP LOCKED` and two
//! replicas deliver the same row twice; drop `ORDER BY created_at` and a busy
//! outbox starves its oldest entries; increment attempts in a second statement
//! and a crash mid-send strands the row in an in-flight state forever.
//!
//! So the claim lives here once, and each drainer supplies only the three
//! things that genuinely differ.

/// One outbox table's claim, rendered from the shared template.
///
/// Every field is a compile-time literal supplied by a drainer in this crate —
/// these are interpolated into SQL, so they must never come from a request.
/// The values that *do* come from callers (attempt budget, batch size) stay
/// bound parameters, `$1` and `$2`.
pub struct OutboxClaim<'a> {
    /// The outbox table.
    pub table: &'a str,
    /// Its primary key, used both to lock and to return.
    pub id_column: &'a str,
    /// The `RETURNING` projection: the id column plus whatever the drainer
    /// needs to deliver the row.
    pub returning: &'a str,
}

impl<'a> OutboxClaim<'a> {
    /// Claim up to `$2` undelivered rows that still have attempts left, oldest
    /// first, incrementing `delivery_attempts` atomically as they are claimed.
    ///
    /// The increment is part of the claim on purpose: a claim that dies
    /// mid-send costs one attempt and is retried on the next flush, rather than
    /// leaving a row that nobody will ever pick up again. Rows past the budget
    /// are left behind as dead letters, visible through `last_delivery_error`.
    ///
    /// Deliberately *not* shared: retention, delivery, and the attempt budget
    /// itself. Those are per-outbox policy — telemetry and customer
    /// notifications have every right to differ — while this is a concurrency
    /// invariant that nothing should differ on.
    pub fn sql(&self) -> String {
        let Self {
            table,
            id_column,
            returning,
        } = self;
        format!(
            "UPDATE {table} SET delivery_attempts = delivery_attempts + 1 \
             WHERE {id_column} IN ( \
                 SELECT {id_column} FROM {table} \
                 WHERE delivered = FALSE AND delivery_attempts < $1 \
                 ORDER BY created_at \
                 LIMIT $2 \
                 FOR UPDATE SKIP LOCKED \
             ) \
             RETURNING {returning}"
        )
    }
}

#[cfg(test)]
mod tests {
    use super::OutboxClaim;

    /// The template's whole reason to exist is the four clauses that are easy
    /// to omit by hand. Assert they are present rather than assert the exact
    /// string, so reformatting the SQL doesn't fail the test that guards them.
    #[test]
    fn claim_keeps_the_concurrency_clauses() {
        let sql = OutboxClaim {
            table: "some_outbox",
            id_column: "some_id",
            returning: "some_id, payload",
        }
        .sql();

        assert!(sql.contains("FOR UPDATE SKIP LOCKED"), "{sql}");
        assert!(sql.contains("ORDER BY created_at"), "{sql}");
        assert!(
            sql.contains("delivery_attempts = delivery_attempts + 1"),
            "{sql}"
        );
        assert!(
            sql.contains("delivered = FALSE AND delivery_attempts < $1"),
            "{sql}"
        );
        assert!(sql.contains("RETURNING some_id, payload"), "{sql}");
    }
}

from __future__ import annotations
from typing import Optional


class CxDB:
    """Read-only access to nano-bank's Postgres for CX metrics + cx_issues.

    Schema notes (verified against the live DB): customers PK is `customer_id`;
    `transactions` link to a customer via `initiated_by` (there is no account_id on
    the header); KYC-complete is `kyc_completed_at IS NOT NULL`; Interac's completed
    status is `deposited`.
    """

    def __init__(self, db_params: Optional[dict] = None):
        self._db = db_params

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(**self._db)
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def customers_onboarding(self) -> list[dict]:
        return self.rows(
            "SELECT count(*) AS total,"
            " count(*) FILTER (WHERE kyc_completed_at IS NOT NULL) AS kyc_completed,"
            " count(*) FILTER (WHERE kyc_completed_at IS NULL) AS kyc_pending"
            " FROM customers")

    def accounts_activation(self) -> list[dict]:
        return self.rows(
            "SELECT count(*) AS total,"
            " count(*) FILTER (WHERE status = 'active') AS active,"
            " count(*) FILTER (WHERE status = 'pending_activation') AS pending_activation"
            " FROM accounts")

    def product_activity(self, window_days: int) -> list[dict]:
        # distinct customers who initiated a transaction tagged with each product
        return self.rows(
            "SELECT product, count(DISTINCT initiated_by) AS customers FROM transactions"
            " WHERE product IS NOT NULL AND initiated_by IS NOT NULL"
            " AND created_at >= now() - (%s || ' days')::interval"
            " GROUP BY product", (window_days,))

    def active_customer_count(self, window_days: int) -> list[dict]:
        return self.rows(
            "SELECT count(DISTINCT initiated_by) AS active_customers FROM transactions"
            " WHERE initiated_by IS NOT NULL"
            " AND created_at >= now() - (%s || ' days')::interval", (window_days,))

    def transaction_outcomes(self, window_days: int) -> list[dict]:
        return self.rows(
            "SELECT coalesce(product,'unknown') AS product,"
            " count(*) AS total, count(*) FILTER (WHERE status = 'failed') AS failed"
            " FROM transactions"
            " WHERE created_at >= now() - (%s || ' days')::interval"
            " GROUP BY product", (window_days,))

    def interac_outcomes(self, window_days: int) -> list[dict]:
        return self.rows(
            "SELECT status::text AS status, count(*) AS n FROM interac_etransfers"
            " WHERE created_at >= now() - (%s || ' days')::interval"
            " GROUP BY status", (window_days,))

    def customer_recency(self) -> list[dict]:
        return self.rows(
            "SELECT c.customer_id AS customer_id, max(t.created_at) AS last_txn"
            " FROM customers c LEFT JOIN transactions t ON t.initiated_by = c.customer_id"
            " GROUP BY c.customer_id")

    def total_customers(self) -> int:
        return self.rows("SELECT count(*) AS n FROM customers")[0]["n"]

    def issue_rows(self) -> list[dict]:
        return self.rows(
            "SELECT id::text, customer_id::text, category::text, severity::text,"
            " summary, detail, status::text, created_at, resolved_at FROM cx_issues"
            " ORDER BY created_at DESC")

    def issue_by_id(self, issue_id: str) -> Optional[dict]:
        r = self.rows(
            "SELECT id::text, customer_id::text, category::text, severity::text,"
            " summary, detail, status::text, created_at FROM cx_issues WHERE id = %s",
            (issue_id,))
        return r[0] if r else None

    # --- surveys / segments ---------------------------------------------------
    def resolve_segment(self, segment: str, window_days: int) -> list[str]:
        if segment == "all_active":
            rows = self.rows(
                "SELECT DISTINCT initiated_by::text AS c FROM transactions"
                " WHERE initiated_by IS NOT NULL"
                " AND created_at >= now() - (%s || ' days')::interval", (window_days,))
        elif segment.startswith("product:"):
            rows = self.rows(
                "SELECT DISTINCT initiated_by::text AS c FROM transactions"
                " WHERE product = %s AND initiated_by IS NOT NULL"
                " AND created_at >= now() - (%s || ' days')::interval",
                (segment.split(":", 1)[1], window_days))
        elif segment == "has_open_issue":
            # 'has_open_issue' backs a "how satisfied are you with the
            # resolution" CSAT campaign (seed_surveys.py) — a customer whose
            # only open cx_issue is a feature_request hasn't had anything
            # break; they asked for more, which isn't the same signal as an
            # unresolved complaint. Exclude it so the segment (and the
            # sentiment it drives, see open_issue_customers below) means what
            # its own survey question assumes.
            rows = self.rows(
                "SELECT DISTINCT customer_id::text AS c FROM cx_issues"
                " WHERE status <> 'resolved' AND category <> 'feature_request'")
        elif segment == "dormant":
            rows = self.rows(
                "SELECT c.customer_id::text AS c FROM customers c"
                " WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.initiated_by = c.customer_id"
                " AND t.created_at >= now() - (%s || ' days')::interval)", (window_days,))
        else:
            return []
        return [r["c"] for r in rows]

    def open_issue_customers(self) -> set:
        # See the matching note on resolve_segment's 'has_open_issue' branch:
        # an open feature_request is an unmet want, not dissatisfaction, so it
        # doesn't belong in the detractor signal customer_sentiment() builds
        # from this set.
        return {r["c"] for r in self.rows(
            "SELECT DISTINCT customer_id::text AS c FROM cx_issues"
            " WHERE status <> 'resolved' AND category <> 'feature_request'")}

    def dormant_customers(self, window_days: int) -> set:
        return {r["c"] for r in self.rows(
            "SELECT c.customer_id::text AS c FROM customers c"
            " WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.initiated_by = c.customer_id"
            " AND t.created_at >= now() - (%s || ' days')::interval)", (window_days,))}

    def insert_campaign(self, instrument: str, segment: str, question: str,
                        source: str = "demo_seed") -> str:
        import psycopg2
        conn = psycopg2.connect(**self._db)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO survey_campaigns (instrument, segment, question, source)"
                    " VALUES (%s,%s,%s,%s) RETURNING id::text",
                    (instrument, segment, question, source))
                return cur.fetchone()[0]
        finally:
            conn.close()

    def insert_responses(self, campaign_id: str, rows: list) -> None:
        # rows: [(customer_id, score), ...]
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(**self._db)
        try:
            with conn, conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO survey_responses (campaign_id, customer_id, score)"
                    " VALUES %s", [(campaign_id, c, s) for c, s in rows])
        finally:
            conn.close()

    def campaigns(self) -> list[dict]:
        return self.rows(
            "SELECT sc.id::text, sc.instrument::text AS instrument, sc.segment, sc.question,"
            " sc.status, count(sr.id) AS responses FROM survey_campaigns sc"
            " LEFT JOIN survey_responses sr ON sr.campaign_id = sc.id"
            " GROUP BY sc.id ORDER BY sc.created_at DESC")

    def survey_scores(self, campaign_id: Optional[str] = None,
                      instrument: Optional[str] = None) -> list[int]:
        if campaign_id:
            rows = self.rows("SELECT score FROM survey_responses WHERE campaign_id = %s",
                             (campaign_id,))
        elif instrument:
            rows = self.rows(
                "SELECT sr.score FROM survey_responses sr JOIN survey_campaigns sc"
                " ON sc.id = sr.campaign_id WHERE sc.instrument = %s", (instrument,))
        else:
            rows = self.rows("SELECT score FROM survey_responses")
        return [r["score"] for r in rows]

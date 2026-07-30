from __future__ import annotations
from decimal import Decimal
from typing import Optional


class FinanceDB:
    """Read-only access to nano-bank's Postgres + the one gl_snapshots writer."""

    def __init__(self, db_params: Optional[dict] = None):
        self._db = db_params

    def _rows(self, sql: str, params: tuple) -> list[dict]:
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

    def _exec(self, sql: str, params: tuple) -> None:
        import psycopg2
        conn = psycopg2.connect(**self._db)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        self._exec(
            "-- ensure_schema\n"
            "CREATE TABLE IF NOT EXISTS gl_snapshots ("
            " period TEXT NOT NULL, role TEXT NOT NULL, balance NUMERIC(20,2) NOT NULL,"
            " captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            " PRIMARY KEY (period, role))", ())

    def write_snapshot(self, period: str, balances: dict) -> None:
        for role, bal in balances.items():
            self._exec(
                "-- write_snapshot\n"
                "INSERT INTO gl_snapshots (period, role, balance) VALUES (%s,%s,%s) "
                "ON CONFLICT (period, role) DO UPDATE SET balance = EXCLUDED.balance,"
                " captured_at = now()", (period, role, bal))

    def read_snapshot(self, period: str) -> dict:
        rows = self._rows(
            "-- read_snapshot\nSELECT role, balance FROM gl_snapshots WHERE period = %s",
            (period,))
        return {r["role"]: Decimal(str(r["balance"])) for r in rows}

    def list_periods(self) -> list:
        rows = self._rows(
            "-- list_periods\nSELECT DISTINCT period FROM gl_snapshots ORDER BY period", ())
        return [r["period"] for r in rows]

    def accruals(self, start: str, end: str) -> list:
        rows = self._rows(
            "-- accruals\nSELECT product, cost_centre, side, SUM(amount) AS amount "
            "FROM interest_accruals WHERE accrual_date >= %s AND accrual_date < %s "
            "GROUP BY product, cost_centre, side", (start, end))
        return [dict(r, amount=Decimal(str(r["amount"]))) for r in rows]

    def fees(self, start: str, end: str) -> list:
        rows = self._rows(
            "-- fees\nSELECT product, cost_centre, SUM(amount) AS amount "
            "FROM transactions WHERE transaction_type = 'fee' "
            "AND created_at >= %s AND created_at < %s "
            "AND product IS NOT NULL GROUP BY product, cost_centre", (start, end))
        return [dict(r, amount=Decimal(str(r["amount"]))) for r in rows]

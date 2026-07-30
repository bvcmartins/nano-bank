"""Live smoke helper for the reporting service (invoked by verify-reports.sh).

Usage:
  python -m finance.smoke baseline <period>          # snapshot before activity
  python -m finance.smoke report   <period> <prior>  # snapshot after + assert
"""
from __future__ import annotations
import datetime as dt
import sys
from decimal import Decimal

from .config import Settings
from .db import FinanceDB
from . import ledger_client, snapshots, reports


def _range(period: str):
    y, m = (int(x) for x in period.split("-"))
    start = dt.date(y, m, 1)
    end = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    return start.isoformat(), end.isoformat(), (end - start).days


def main() -> int:
    cmd = sys.argv[1]
    settings = Settings.from_env()
    db = FinanceDB(settings.db)
    db.ensure_schema()

    if cmd == "baseline":
        period = sys.argv[2]
        rows = ledger_client.get_balances(settings.nano_bank_api)
        out = snapshots.close_period(period, rows, db)
        print(f"baseline snapshot {period}: {out['roles_captured']} roles")
        return 0

    if cmd == "report":
        period, prior = sys.argv[2], sys.argv[3]
        rows = ledger_client.get_balances(settings.nano_bank_api)
        snapshots.close_period(period, rows, db)
        snap = db.read_snapshot(period)
        prior_snap = db.read_snapshot(prior)
        start, end, days = _range(period)

        bs = reports.balance_sheet(snap)
        inc = reports.income_statement(snap, prior_snap)
        nim = reports.nim(snap, prior_snap, days)
        interchange = inc["income"].get("InterchangeIncome", Decimal(0))
        seg = reports.segment_pnl(db.accruals(start, end), db.fees(start, end), interchange)

        print(f"BALANCE SHEET  balanced={bs['balanced']} "
              f"assets={bs['total_assets']} L+E={bs['total_liabilities_equity']}")
        print(f"INCOME STMT    income={inc['total_income']} "
              f"expense={inc['total_expense']} net={inc['net_income']}")
        print(f"NIM            net_interest={nim['net_interest']} "
              f"avg_earning_assets={nim['avg_earning_assets']} nim={nim['nim']}")
        print(f"SEGMENT P&L    total_income={seg['total_income']} "
              f"segments={len(seg['segments'])}")

        assert bs["balanced"], (
            f"balance sheet not balanced: {bs['total_assets']} vs "
            f"{bs['total_liabilities_equity']}")
        assert inc["total_income"] > 0, "no income recognized between snapshots"
        assert seg["total_income"] > 0, "no segment income in period"
        print("REPORTS SMOKE: PASS")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

from decimal import Decimal as D
from finance import snapshots


class RecorderDB:
    def __init__(self):
        self.written = None

    def write_snapshot(self, period, balances):
        self.written = (period, balances)


def test_close_period_maps_codes_to_roles():
    rows = [
        {"account": "CASH_RESERVES", "balance": "1000.00"},
        {"account": "DEPOSITS", "balance": "-1000.00"},
        {"account": "MYSTERY", "balance": "5.00"},   # unrecognized -> skipped
    ]
    db = RecorderDB()
    out = snapshots.close_period("2026-07", rows, db)
    period, balances = db.written
    assert period == "2026-07"
    assert balances == {"CashReserves": D("1000.00"), "CustomerDeposits": D("-1000.00")}
    assert out["roles_captured"] == 2

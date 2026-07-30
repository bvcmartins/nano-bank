from decimal import Decimal as D
from finance.db import FinanceDB


class FakeDB(FinanceDB):
    def __init__(self, canned):
        super().__init__(db_params=None)
        self._canned = canned
        self.writes = []

    def _rows(self, sql, params):
        return self._canned.get(sql.split("\n", 1)[0].strip(), [])

    def _exec(self, sql, params):
        self.writes.append((sql.split("\n", 1)[0].strip(), params))


def test_read_snapshot_shapes_dict():
    db = FakeDB({"-- read_snapshot": [
        {"role": "CashReserves", "balance": D("1000")},
        {"role": "CustomerDeposits", "balance": D("-1000")},
    ]})
    snap = db.read_snapshot("2026-07")
    assert snap == {"CashReserves": D("1000"), "CustomerDeposits": D("-1000")}


def test_write_snapshot_upserts_each_role():
    db = FakeDB({})
    db.write_snapshot("2026-07", {"CashReserves": D("1000"), "CustomerDeposits": D("-1000")})
    assert len(db.writes) == 2
    assert all(w[0] == "-- write_snapshot" for w in db.writes)

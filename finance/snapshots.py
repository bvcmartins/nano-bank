"""Period-close: capture the core GL trial balance as a backend-agnostic snapshot."""
from __future__ import annotations
from decimal import Decimal
from . import roles


def close_period(period: str, balances_rows: list, db) -> dict:
    """Map /ledger/balances rows to semantic roles and store the snapshot.

    Balances are stored in debit-credit convention. If the one-time check in the
    plan (Task 4 Step 1) shows the core returns magnitudes rather than signed
    (debit - credit) balances, negate credit-normal roles here before storing.
    """
    snapshot: dict[str, Decimal] = {}
    for row in balances_rows:
        role = roles.role_for_code(row["account"])
        if role is None:
            continue
        snapshot[role] = Decimal(str(row["balance"]))
    db.write_snapshot(period, snapshot)
    return {"period": period, "roles_captured": len(snapshot)}

"""Pure period-report math. All inputs are plain data (dicts/lists of Decimal);
no DB or IO here, so every function is unit-testable in isolation. Balances use
the debit-credit convention (assets/expenses +, liabilities/equity/income -).
"""
from __future__ import annotations
from decimal import Decimal
from . import roles


def _line_total(snapshot: dict, line: str, *, credit_normal: bool) -> dict:
    out = {}
    for role, bal in snapshot.items():
        if roles.STATEMENT_LINE.get(role) != line:
            continue
        out[role] = -bal if credit_normal else bal
    return out


def balance_sheet(snapshot: dict) -> dict:
    assets = _line_total(snapshot, "asset", credit_normal=False)
    liabilities = _line_total(snapshot, "liability", credit_normal=True)
    equity = _line_total(snapshot, "equity", credit_normal=True)
    # Income/expense are not closed to retained earnings in this GL; fold current
    # earnings (income - expense to date) into equity so the sheet balances
    # (A = L + E + net income — the trial-balance identity).
    income = _line_total(snapshot, "income", credit_normal=True)
    expense = _line_total(snapshot, "expense", credit_normal=False)
    equity = dict(equity)
    equity["CurrentEarnings"] = (
        sum(income.values(), Decimal(0)) - sum(expense.values(), Decimal(0)))
    ta = sum(assets.values(), Decimal(0))
    tle = sum(liabilities.values(), Decimal(0)) + sum(equity.values(), Decimal(0))
    return {
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "total_assets": ta, "total_liabilities_equity": tle,
        "balanced": ta == tle,
    }


def _flow(closing: dict, opening: dict, role: str, *, credit_normal: bool) -> Decimal:
    delta = closing.get(role, Decimal(0)) - opening.get(role, Decimal(0))
    return -delta if credit_normal else delta


def income_statement(closing: dict, opening: dict) -> dict:
    income = {r: _flow(closing, opening, r, credit_normal=True) for r in roles.INCOME_ROLES
              if r in closing or r in opening}
    expense = {r: _flow(closing, opening, r, credit_normal=False) for r in roles.EXPENSE_ROLES
               if r in closing or r in opening}
    ti = sum(income.values(), Decimal(0))
    te = sum(expense.values(), Decimal(0))
    return {"income": income, "expense": expense,
            "total_income": ti, "total_expense": te, "net_income": ti - te}


# An annualised net-interest margin outside this band almost always means the
# earning-asset denominator is too thin for the period (a partial/first period,
# or an asset class — overdraft/loans/treasury — not yet posting to its granular
# GL role) rather than a real >35% margin. Surface it so the CFO agent treats
# the figure as provisional instead of acting on a denominator artefact. Tunable.
_NIM_PLAUSIBLE_MAX = Decimal("0.35")


def nim(closing: dict, opening: dict, days: int) -> dict:
    net_interest = (_flow(closing, opening, "InterestIncome", credit_normal=True)
                    - _flow(closing, opening, "InterestExpense", credit_normal=False))
    avg = Decimal(0)
    for role in roles.EARNING_ASSET_ROLES:
        avg += (opening.get(role, Decimal(0)) + closing.get(role, Decimal(0))) / Decimal(2)

    if avg <= 0:
        margin, provisional, reason = Decimal(0), True, "no-earning-assets"
    elif days <= 0:
        margin, provisional, reason = Decimal(0), True, "empty-period"
    else:
        margin = net_interest / avg * (Decimal(365) / Decimal(days))
        provisional = abs(margin) > _NIM_PLAUSIBLE_MAX
        reason = "implausible-margin" if provisional else None

    return {"net_interest": net_interest, "avg_earning_assets": avg,
            "nim": margin, "provisional": provisional, "reason": reason}


def segment_pnl(accruals: list, fees: list, interchange_total: Decimal) -> dict:
    segments: dict[tuple, dict] = {}

    def bucket(product, cost_centre):
        return segments.setdefault(
            (product, cost_centre), {"income": Decimal(0), "expense": Decimal(0)})

    for a in accruals:
        b = bucket(a["product"], a["cost_centre"])
        b["income" if a["side"] == "income" else "expense"] += a["amount"]
    for f in fees:
        bucket(f["product"], f["cost_centre"])["income"] += f["amount"]
    if interchange_total:
        bucket("card", "payments")["income"] += interchange_total

    ti = sum((s["income"] for s in segments.values()), Decimal(0))
    te = sum((s["expense"] for s in segments.values()), Decimal(0))
    return {"segments": segments, "total_income": ti, "total_expense": te,
            "net_income": ti - te}

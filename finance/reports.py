"""Pure period-report math. All inputs are plain data (dicts/lists of Decimal);
no DB or IO here, so every function is unit-testable in isolation. Balances use
the debit-credit convention (assets/expenses +, liabilities/equity/income -).
"""
from __future__ import annotations
from decimal import Decimal
from typing import Optional
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


_SEGMENT_SOURCE = (
    "segment figures are read from nano-bank's operational tables "
    "(interest_accruals, transactions), NOT the GL trial balance — interchange "
    "is the one line that is GL-derived. The two populations differ and are "
    "cleared on different schedules, so segment totals need not tie to "
    "income_statement; see `reconciliation`")

_UNRECONCILED = (
    "segment net income does NOT tie to the GL income statement. The segments "
    "carry activity the GL has not booked (or vice versa) — do not present "
    "these figures as the bank's P&L, and do not rank segments on value "
    "creation, until the difference is explained")


def segment_pnl(accruals: list, fees: list, interchange_total: Decimal,
                gl_net_income: Optional[Decimal] = None) -> dict:
    """P&L by product and cost-centre.

    Takes `gl_net_income` so the report can reconcile itself against the GL
    income statement. It is optional only so the pure math stays callable
    without a snapshot; when it is absent the result says so rather than
    implying the segments tie.
    """
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
    ni = ti - te

    rec: dict = {"segment_net_income": ni, "gl_net_income": gl_net_income,
                 "difference": None, "reconciled": None,
                 "note": ("no GL net income supplied — these segments are "
                          "unreconciled against the income statement")}
    if gl_net_income is not None:
        diff = ni - gl_net_income
        rec["difference"] = diff
        rec["reconciled"] = diff == 0
        rec["note"] = ("segment net income ties to the GL income statement"
                       if not diff else _UNRECONCILED)

    return {"segments": segments, "total_income": ti, "total_expense": te,
            "net_income": ni, "source": _SEGMENT_SOURCE, "reconciliation": rec}

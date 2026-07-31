from decimal import Decimal as D
from finance import reports


# Balances in debit-credit convention: assets/expenses +, liab/equity/income -.
def _snapshot(**kw):
    return {k: D(v) for k, v in kw.items()}


def test_balance_sheet_balances():
    snap = _snapshot(
        CashReserves="1000", CardReceivable="500",   # assets +1500
        CustomerDeposits="-1400",                     # liability 1400
        Capital="-100",                               # equity 100
    )
    bs = reports.balance_sheet(snap)
    assert bs["total_assets"] == D("1500")
    assert bs["total_liabilities_equity"] == D("1500")
    assert bs["balanced"] is True


def test_balance_sheet_folds_unclosed_earnings_into_equity():
    # Income/expense are not closed to retained earnings in this GL, so the sheet
    # only balances when current earnings (income - expense) count as equity.
    # Trial balance (debit-credit): assets 1500 + expense 40 = deposits 1400
    # + capital 100 + income 40.
    snap = _snapshot(
        CashReserves="1500",           # asset 1500
        OperatingExpense="40",         # expense 40 (debit +)
        CustomerDeposits="-1400",      # liability 1400
        Capital="-100",                # equity 100
        FeeIncome="-40",               # income 40 (credit -)
    )
    bs = reports.balance_sheet(snap)
    assert bs["total_assets"] == D("1500")
    assert bs["equity"]["CurrentEarnings"] == D("0")   # income 40 - expense 40
    assert bs["balanced"] is True


def test_income_statement_period_flow():
    opening = _snapshot(InterestIncome="-100", InterestExpense="20", FeeIncome="-5")
    closing = _snapshot(InterestIncome="-130", InterestExpense="26", FeeIncome="-8")
    inc = reports.income_statement(closing, opening)
    # income flow: InterestIncome 30, FeeIncome 3 -> 33; expense 6; net 27
    assert inc["total_income"] == D("33")
    assert inc["total_expense"] == D("6")
    assert inc["net_income"] == D("27")


def test_nim():
    opening = _snapshot(CardReceivable="1000", InterestIncome="0", InterestExpense="0")
    closing = _snapshot(CardReceivable="1000", InterestIncome="-30", InterestExpense="6")
    out = reports.nim(closing, opening, days=30)
    assert out["net_interest"] == D("24")            # 30 income - 6 expense
    assert out["avg_earning_assets"] == D("1000")
    # annualised: 24/1000 * 365/30
    assert out["nim"] == (D("24") / D("1000") * (D("365") / D("30")))
    assert out["provisional"] is False               # ~29% margin is plausible
    assert out["reason"] is None


def test_nim_counts_accrued_interest_in_earning_base():
    # Accrued-but-uncapitalised card interest is an earning asset.
    opening = _snapshot(AccruedInterestReceivable="400", InterestIncome="0")
    closing = _snapshot(AccruedInterestReceivable="600", InterestIncome="-10")
    out = reports.nim(closing, opening, days=30)
    assert out["avg_earning_assets"] == D("500")     # (400 + 600) / 2


def test_nim_flags_thin_denominator_as_provisional():
    # Full interest income against a sliver of earning assets annualises to an
    # implausible margin — the report must flag it rather than assert it.
    opening = _snapshot(CardReceivable="1", InterestIncome="0")
    closing = _snapshot(CardReceivable="1", InterestIncome="-30")
    out = reports.nim(closing, opening, days=30)
    assert out["provisional"] is True
    assert out["reason"] == "implausible-margin"


def test_nim_no_earning_assets_is_provisional_zero():
    opening = _snapshot(InterestIncome="0")
    closing = _snapshot(InterestIncome="-30")
    out = reports.nim(closing, opening, days=30)
    assert out["nim"] == D("0")
    assert out["provisional"] is True
    assert out["reason"] == "no-earning-assets"


def test_segment_pnl_reconciles_with_interchange():
    accruals = [
        {"product": "deposit", "cost_centre": "deposits", "side": "expense", "amount": D("6")},
        {"product": "card", "cost_centre": "lending", "side": "income", "amount": D("30")},
    ]
    fees = [{"product": "payment", "cost_centre": "payments", "amount": D("3")}]
    out = reports.segment_pnl(accruals, fees, interchange_total=D("12"))
    # card/payments gets the interchange 12; total income = 30 + 3 + 12 = 45
    assert out["total_income"] == D("45")
    assert out["total_expense"] == D("6")
    assert out["segments"][("card", "payments")]["income"] == D("12")


def test_segment_pnl_declares_where_its_numbers_come_from():
    """Segment figures are read from nano-bank's operational tables while the
    income statement is read from the GL snapshot — two different databases,
    cleared on different schedules. A caller that doesn't know that will quote
    segment net income as P&L."""
    out = reports.segment_pnl([], [], interchange_total=D("0"))
    src = out["source"].lower()
    assert "operational" in src and "not the gl" in src
    assert "interchange" in src          # the one line that IS GL-derived


def test_segment_pnl_reconciles_against_the_income_statement():
    accruals = [{"product": "card", "cost_centre": "lending",
                 "side": "income", "amount": D("30")}]
    out = reports.segment_pnl(accruals, [], interchange_total=D("0"),
                              gl_net_income=D("30"))
    assert out["reconciliation"]["reconciled"] is True
    assert out["reconciliation"]["difference"] == D("0")


def test_segment_pnl_flags_an_unreconciled_gap():
    """The demo's GL is reset each run but the operational tables are not, so
    stale fees inflate the segments. The gap has to be stated, not left for the
    reader to notice."""
    accruals = [{"product": "card", "cost_centre": "lending",
                 "side": "income", "amount": D("11988")}]
    out = reports.segment_pnl(accruals, [], interchange_total=D("0"),
                              gl_net_income=D("1448"))
    rec = out["reconciliation"]
    assert rec["reconciled"] is False
    assert rec["segment_net_income"] == D("11988")
    assert rec["gl_net_income"] == D("1448")
    assert rec["difference"] == D("10540")
    assert "do not" in rec["note"].lower()


def test_segment_pnl_without_a_gl_figure_says_it_is_unreconciled():
    out = reports.segment_pnl([], [], interchange_total=D("0"))
    assert out["reconciliation"]["reconciled"] is None

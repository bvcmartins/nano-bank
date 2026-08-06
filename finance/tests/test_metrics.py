from decimal import Decimal as D
from finance import metrics
from finance.config import RiskConfig

RC = RiskConfig.default()


def _assets():
    # closing balances, debit-normal (assets +)
    return {
        "CardReceivable": D("10000"),
        "OverdraftReceivable": D("4000"),
        "LoansReceivable": D("6000"),
        "TreasuryPlacement": D("5000"),
        "CashReserves": D("2000"),
    }


def test_economic_capital_rwa_and_ec():
    ec = metrics.economic_capital(_assets(), RC)
    # RWA: card .75*10000=7500, od 1*4000=4000, loan 1*6000=6000,
    #      treasury .20*5000=1000, cash 0*2000=0  -> 18500
    assert ec["total_rwa"] == D("18500.00")
    assert ec["rwa"]["CardReceivable"] == D("7500.00")
    assert ec["economic_capital"] == D("18500.00") * D("0.10")


def test_expected_loss():
    el = metrics.expected_loss(_assets(), RC)
    # .03*10000 + .02*4000 + .015*6000 = 300 + 80 + 90 = 470
    assert el == D("300.00") + D("80.00") + D("90.000")


def test_raroc_components():
    closing = dict(_assets(),
                   InterestIncome=D("-1000"), InterestExpense=D("200"),
                   OperatingExpense=D("100"), FeeIncome=D("-50"))
    opening = {"InterestIncome": D("0"), "InterestExpense": D("0"),
               "OperatingExpense": D("0"), "FeeIncome": D("0")}
    out = metrics.raroc(closing, opening, days=30, risk=RC)
    # income statement net income: income (1000+50) - expense (200+100) = 750
    assert out["net_income"] == D("750")
    assert out["net_income_annualized"] == D("750") * D("365") / D("30")
    assert out["expected_loss"] == D("470.000")
    assert out["economic_capital"] == D("18500.00") * D("0.10")
    assert out["risk_adjusted_return"] == (
        out["net_income_annualized"] - out["expected_loss"])
    assert out["raroc"] == out["risk_adjusted_return"] / out["economic_capital"]


def test_raroc_zero_capital_is_safe():
    out = metrics.raroc({}, {}, days=30, risk=RC)
    assert out["economic_capital"] == D("0.00")
    assert out["raroc"] is None


def _ann(x):
    # multiply-first annualisation, matching the implementation
    return x * D("365") / D("30")


def test_key_ratios():
    closing = {
        "CashReserves": D("5000"), "CardReceivable": D("10000"),
        "TreasuryPlacement": D("5000"),
        "CustomerDeposits": D("-16000"),          # deposits 16000
        "Capital": D("-3000"),                    # equity 3000 (ex earnings)
        "InterestIncome": D("-1000"), "InterestExpense": D("200"),
        "OperatingExpense": D("100"), "FeeIncome": D("-50"),
    }
    opening = {
        "CardReceivable": D("10000"), "TreasuryPlacement": D("5000"),
        "CustomerDeposits": D("-16000"),
        "InterestIncome": D("0"), "InterestExpense": D("0"),
        "OperatingExpense": D("0"), "FeeIncome": D("0"),
    }
    r = metrics.key_ratios(closing, opening, days=30, risk=RC)
    # net income = income(1050) - expense(300) = 750; annualised = 750*365/30
    ni_ann = _ann(D("750"))
    # total assets = 5000+10000+5000 = 20000
    assert r["roa"] == ni_ann / D("20000")
    # capital base = equity excluding CurrentEarnings = 3000
    assert r["roe"] == ni_ann / D("3000")
    # efficiency = opex(100) / total_revenue(net interest 800 + fee 50) = 100/850
    assert r["efficiency_ratio"] == D("100") / D("850")
    # LDR = loans(10000) / deposits(16000)
    assert r["loan_to_deposit"] == D("10000") / D("16000")
    # cost of funds = interest_expense annualised / avg deposits(16000)
    assert r["cost_of_funds"] == _ann(D("200")) / D("16000")


def test_key_ratios_guard_zero_denominators():
    r = metrics.key_ratios({}, {}, days=30, risk=RC)
    assert r["roa"] is None
    assert r["loan_to_deposit"] is None


def test_financial_health_bundle_keys():
    closing = {"CashReserves": D("100"), "Capital": D("-100"),
               "InterestIncome": D("-10")}
    opening = {"InterestIncome": D("0")}
    fh = metrics.financial_health(closing, opening, days=30, risk=RC)
    assert set(fh) == {"balance_sheet", "income_statement", "nim",
                       "key_ratios", "raroc"}
    assert fh["raroc"]["net_income"] == D("10")


def test_every_asset_role_carries_a_risk_weight():
    """Assets outside the configured weights must NOT be treated as risk-free."""
    snap = {"Bank": D("1000000"), "Receivable": D("2000"),
            "CashReserves": D("500"), "CustomerDeposits": D("-900000")}
    ec = metrics.economic_capital(snap, RC)
    # Bank is an interbank claim (20%); generic Receivable falls back to 100%;
    # cash reserves stay 0%; the deposit liability is not an asset at all.
    assert ec["rwa"]["Bank"] == D("200000.00")
    assert ec["rwa"]["Receivable"] == D("2000.00")
    assert ec["rwa"]["CashReserves"] == D("0.00")
    assert "CustomerDeposits" not in ec["rwa"]
    assert ec["total_rwa"] == D("202000.00")


def test_default_asset_weight_is_configurable():
    rc = RiskConfig.from_env({"RISK_DEFAULT_ASSET_WEIGHT": "0.50"})
    ec = metrics.economic_capital({"Receivable": D("1000")}, rc)
    assert ec["rwa"]["Receivable"] == D("500.00")


def test_raroc_declares_its_provisioning_basis():
    """The books carry no loan-loss provision, so net income is pre-provision
    and RAROC — not ROE — is where credit cost lands. Say so in the output."""
    out = metrics.raroc({"LoansReceivable": D("1000")}, {}, days=30, risk=RC)
    assert "pre-provision" in out["basis"]


def test_raroc_labels_the_periodicity_of_every_figure():
    """net_income covers the period; expected_loss is annual. A reader that
    nets one against the other turns a profitable month into a fake loss, so
    the units have to travel with the numbers."""
    out = metrics.raroc({"LoansReceivable": D("1000")}, {}, days=31, risk=RC)
    units = out["units"]
    assert set(units) >= {"net_income", "net_income_annualized", "expected_loss",
                          "risk_adjusted_return", "economic_capital", "raroc"}
    assert "31-day period" in units["net_income"]
    assert "annual" in units["expected_loss"]
    assert out["period_days"] == 31


def test_raroc_offers_the_period_equivalent_expected_loss():
    """So nobody has to rescale the annual figure by hand to compare it with
    the period's net income."""
    out = metrics.raroc({"LoansReceivable": D("1000")}, {}, days=73, risk=RC)
    assert out["expected_loss"] == D("15.000")            # 1000 * 0.015
    assert out["expected_loss_period"] == D("15.000") * D("73") / D("365")


def test_economic_capital_reports_the_weights_it_used():
    ec = metrics.economic_capital(_assets(), RC)
    assert ec["risk_weights"]["CardReceivable"] == D("0.75")
    assert ec["assumed_weight_roles"] == []


def test_economic_capital_flags_roles_that_fell_back_to_the_default():
    """A weight nobody configured is an assumption, not a policy — the caller
    must be able to tell the two apart. `Receivable` is a real instance: it is
    a known asset with no entry in the weight table, and it was one of the
    roles that silently vanished from RWA before assets were driven off the
    snapshot."""
    ec = metrics.economic_capital({"Receivable": D("1000")}, RC)
    assert ec["assumed_weight_roles"] == ["Receivable"]
    assert ec["risk_weights"]["Receivable"] == RC.default_asset_weight


def test_economic_capital_flags_unclassified_roles_it_dropped():
    """A role `STATEMENT_LINE` doesn't classify at all (e.g. a new GL role added
    without updating the map) carries a balance but is dropped from RWA. That is
    the same silent blind spot as an unweighted asset, so a nonzero one must be
    named rather than vanish."""
    ec = metrics.economic_capital({"MysteryLedger": D("5000")}, RC)
    assert ec["unclassified_roles"] == ["MysteryLedger"]
    assert ec["total_rwa"] == D("0")   # it was dropped from the charge


def test_economic_capital_does_not_flag_zero_or_known_nonasset_roles():
    # A zero-balance unclassified role is not worth flagging; a classified
    # non-asset (a liability) is correctly excluded, not "unclassified".
    ec = metrics.economic_capital(
        {"MysteryLedger": D("0"), "CustomerDeposits": D("-4000")}, RC)
    assert ec["unclassified_roles"] == []


def test_raroc_reports_the_credit_exposure_and_loss_rate():
    """Otherwise the only way to say 'expected loss is ~1.8% of the book' is to
    divide by hand — which is exactly the arithmetic callers are told not to do."""
    out = metrics.raroc(_assets(), {}, days=30, risk=RC)
    # exposure = the roles carrying a loss rate: 10000 + 4000 + 6000
    assert out["credit_exposure"] == D("20000")
    assert out["expected_loss"] == D("470.000")
    assert out["expected_loss_rate"] == D("470.000") / D("20000")
    assert "annual ratio" in out["units"]["expected_loss_rate"]


def test_expected_loss_rate_is_none_without_exposure():
    out = metrics.raroc({"CashReserves": D("100")}, {}, days=30, risk=RC)
    assert out["credit_exposure"] == D("0")
    assert out["expected_loss_rate"] is None


def test_provision_scenario_annualises_both_sides():
    """The reported ROA/ROE are annualised. Asked what a provision would do,
    the agent computed the 'after' figures off the raw period income and put
    them in the same table as the annualised 'before' — ROE came out 11x too
    small. Both sides have to be annualised the same way."""
    closing = {
        "CashReserves": D("5000"), "CardReceivable": D("10000"),
        "TreasuryPlacement": D("5000"), "CustomerDeposits": D("-16000"),
        "Capital": D("-3000"),
        "InterestIncome": D("-1000"), "InterestExpense": D("200"),
        "OperatingExpense": D("100"), "FeeIncome": D("-50"),
    }
    opening = {"InterestIncome": D("0"), "InterestExpense": D("0"),
               "OperatingExpense": D("0"), "FeeIncome": D("0")}
    out = metrics.provision_scenario(closing, opening, days=30, risk=RC,
                                     provision=D("100"))
    assert out["provision"] == D("100")
    assert out["net_income"] == D("750")
    assert out["net_income_after"] == D("650")
    # assets carry the allowance; the capital base does not move because a
    # provision lands in current earnings, which key_ratios already excludes
    assert out["roa_after"] == _ann(D("650")) / D("19900")
    assert out["roe_after"] == _ann(D("650")) / D("3000")
    assert out["roa_before"] == _ann(D("750")) / D("20000")
    assert out["roe_before"] == _ann(D("750")) / D("3000")
    assert "annual" in out["units"]["roa_after"]


def test_provision_scenario_can_turn_returns_negative():
    closing = {"CashReserves": D("1000"), "Capital": D("-500"),
               "InterestIncome": D("-100")}
    out = metrics.provision_scenario(closing, {}, days=30, risk=RC,
                                     provision=D("400"))
    assert out["net_income_after"] == D("-300")
    assert out["roa_after"] < 0


def test_compute_derived_figures():
    # cost/income ratio, a share, an average — the derived figures the metric
    # tools don't expose, computed deterministically so they stay grounded.
    assert metrics.compute("ratio", [4500, 10000])["result"] == D("0.4500")
    assert metrics.compute("percent", [4500, 10000])["result"] == D("45.00")
    assert metrics.compute("mean", [10, 20, 30])["result"] == D("20.00")
    assert metrics.compute("difference", [1000, 250])["result"] == D("750.00")
    assert "error" in metrics.compute("ratio", [5, 0])       # zero denominator
    assert "error" in metrics.compute("bogus", [1, 2])       # unknown op

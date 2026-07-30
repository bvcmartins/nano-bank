from finance import roles


def test_reverse_map_covers_both_backends():
    assert roles.role_for_code("INT_INCOME") == "InterestIncome"
    assert roles.role_for_code("0000800100") == "InterestIncome"   # legacy saknr
    assert roles.role_for_code("ACCR_INT_PAY") == "AccruedInterestPayable"
    assert roles.role_for_code("0000220000") == "AccruedInterestPayable"
    assert roles.role_for_code("UNKNOWN") is None


def test_statement_classification():
    assert roles.STATEMENT_LINE["CardReceivable"] == "asset"
    assert roles.STATEMENT_LINE["CustomerDeposits"] == "liability"
    assert roles.STATEMENT_LINE["Capital"] == "equity"
    assert roles.STATEMENT_LINE["InterchangeIncome"] == "income"
    assert roles.STATEMENT_LINE["InterestExpense"] == "expense"


def test_tax_accounts_classified():
    # Both cores seed input/output tax; they must classify so the trial balance
    # (and the Balance Sheet) is complete.
    assert roles.role_for_code("INPUT_TAX") == "InputTax"
    assert roles.role_for_code("0000175000") == "InputTax"
    assert roles.role_for_code("OUTPUT_TAX") == "OutputTax"
    assert roles.role_for_code("0000175100") == "OutputTax"
    assert roles.STATEMENT_LINE["InputTax"] == "asset"
    assert roles.STATEMENT_LINE["OutputTax"] == "liability"


def test_earning_assets_exclude_cash_reserves():
    assert "CashReserves" not in roles.EARNING_ASSET_ROLES
    assert roles.EARNING_ASSET_ROLES == {
        "CardReceivable", "OverdraftReceivable", "LoansReceivable", "TreasuryPlacement",
        "AccruedInterestReceivable",
    }

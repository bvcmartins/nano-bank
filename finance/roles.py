"""Backend-agnostic GL role map + statement classification (specs #1-#2).

`/ledger/balances` returns backend-specific codes (modern code or legacy saknr);
this maps both to the semantic role, and the role to its financial-statement line.
"""
from __future__ import annotations

# role -> (modern code, legacy saknr)
_ROLE_CODES: dict[str, tuple[str, str]] = {
    "Bank": ("BANK", "0000113100"),
    "Receivable": ("AR", "0000140000"),
    "Payable": ("AP", "0000160000"),
    "Revenue": ("REVENUE", "0000800000"),
    "Expense": ("EXPENSE", "0000400000"),
    "CashReserves": ("CASH_RESERVES", "0000105000"),
    "CardReceivable": ("CARD_AR", "0000141000"),
    "OverdraftReceivable": ("OVERDRAFT_AR", "0000141500"),
    "LoansReceivable": ("LOANS_AR", "0000142000"),
    "TreasuryPlacement": ("TREASURY", "0000150000"),
    "CustomerDeposits": ("DEPOSITS", "0000210000"),
    "Capital": ("CAPITAL", "0000300000"),
    "RetainedEarnings": ("RETAINED", "0000330000"),
    "InterestIncome": ("INT_INCOME", "0000800100"),
    "InterchangeIncome": ("INTERCHANGE", "0000800200"),
    "FeeIncome": ("FEE_INCOME", "0000800300"),
    "InterestExpense": ("INT_EXPENSE", "0000400100"),
    "OperatingExpense": ("OPEX", "0000400200"),
    "AccruedInterestReceivable": ("ACCR_INT_RECV", "0000141900"),
    "AccruedInterestPayable": ("ACCR_INT_PAY", "0000220000"),
    # Core-native tax accounts (not Ledger-port roles, but present in the trial
    # balance and needed for a complete/balanced Balance Sheet).
    "InputTax": ("INPUT_TAX", "0000175000"),
    "OutputTax": ("OUTPUT_TAX", "0000175100"),
}

_CODE_TO_ROLE: dict[str, str] = {}
for _role, (_m, _l) in _ROLE_CODES.items():
    _CODE_TO_ROLE[_m] = _role
    _CODE_TO_ROLE[_l] = _role


def role_for_code(code: str) -> str | None:
    """Semantic role for a backend GL code, or None if unrecognized."""
    return _CODE_TO_ROLE.get(code)


STATEMENT_LINE: dict[str, str] = {
    "Bank": "asset", "Receivable": "asset", "CashReserves": "asset",
    "CardReceivable": "asset", "OverdraftReceivable": "asset",
    "LoansReceivable": "asset", "TreasuryPlacement": "asset",
    "AccruedInterestReceivable": "asset",
    "InputTax": "asset",
    "Payable": "liability", "CustomerDeposits": "liability",
    "AccruedInterestPayable": "liability", "OutputTax": "liability",
    "Capital": "equity", "RetainedEarnings": "equity",
    "Revenue": "income", "InterestIncome": "income",
    "InterchangeIncome": "income", "FeeIncome": "income",
    "Expense": "expense", "InterestExpense": "expense",
    "OperatingExpense": "expense",
}

EARNING_ASSET_ROLES: set[str] = {
    "CardReceivable", "OverdraftReceivable", "LoansReceivable", "TreasuryPlacement",
}

# Asset roles that bear credit loss — the book that expected_loss is charged
# against. Kept here as a domain classification (like EARNING_ASSET_ROLES) so
# expected_loss/credit_exposure can be driven off the balance sheet rather than
# off the loss-rate config: a new receivable role added here without a rate then
# falls back to the default loss rate and is named, instead of silently
# contributing zero to both loss and exposure. TreasuryPlacement is intentionally
# excluded — an interbank/treasury placement is an earning asset but not a retail
# credit exposure, and defaulting it to the receivable loss rate would overstate.
CREDIT_EXPOSED_ROLES: set[str] = {
    "CardReceivable", "OverdraftReceivable", "LoansReceivable",
    "Receivable", "AccruedInterestReceivable",
}
INCOME_ROLES: set[str] = {r for r, l in STATEMENT_LINE.items() if l == "income"}
EXPENSE_ROLES: set[str] = {r for r, l in STATEMENT_LINE.items() if l == "expense"}

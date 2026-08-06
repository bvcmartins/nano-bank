"""Pure CFO-metric math over period snapshots (debit-credit convention).
No DB/IO — every function is unit-testable in isolation. Money is Decimal.
The RAROC capital model is Basel-lite (finance.config.RiskConfig); spec #5
replaces it behind the same signatures.
"""
from __future__ import annotations
from decimal import Decimal
from . import reports, roles
from .config import RiskConfig


def _safe_div(n: Decimal, d: Decimal):
    return n / d if d else None


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def compute(operation: str, values) -> dict:
    """Deterministic arithmetic over numbers the other tools already returned, so
    a *derived* figure (a ratio, share, average, difference) the metric tools do
    not expose stays tool-grounded instead of the model doing the math itself.

    operation: mean | sum | ratio | percent | difference | product.
    Returns {operation, inputs, result} or {error, …} — never raises."""
    op = (operation or "").strip().lower()
    nums = [_dec(v) for v in (values or [])]
    two_ok = len(nums) >= 2 and nums[1] != 0

    if op in ("mean", "average", "avg"):
        result = (sum(nums) / len(nums)) if nums else None
    elif op == "sum":
        result = sum(nums) if nums else Decimal(0)
    elif op in ("ratio", "divide"):
        result = (nums[0] / nums[1]) if two_ok else None
    elif op in ("percent", "percentage", "share"):
        result = (nums[0] / nums[1] * 100) if two_ok else None
    elif op in ("difference", "subtract"):
        result = (nums[0] - sum(nums[1:])) if nums else None
    elif op in ("product", "multiply"):
        result = Decimal(1)
        for n in nums:
            result *= n
        if not nums:
            result = None
    else:
        return {"error": f"unknown operation '{operation}' "
                "(use mean|sum|ratio|percent|difference|product)"}

    if result is None:
        return {"error": "need valid operands — ratio/percent want two numbers "
                "with a non-zero denominator", "operation": op, "inputs": nums}
    places = Decimal("0.0001") if op in ("ratio", "divide") else Decimal("0.01")
    return {"operation": op, "inputs": nums, "result": result.quantize(places)}


def economic_capital(snapshot: dict, risk: RiskConfig) -> dict:
    """Risk-weight EVERY asset on the balance sheet.

    Assets are driven off the snapshot rather than off the weight table, so a
    role nobody configured (say a new receivable) falls back to the default
    weight instead of silently counting as risk-free.

    The weights used come back with the numbers, and any role that fell through
    to `default_asset_weight` is named: a fallback weight is an assumption, not
    a policy, and a caller reporting it as bank policy would be overstating how
    deliberate the capital charge is.

    A role carrying a balance that `roles.STATEMENT_LINE` classifies as neither
    asset nor anything else (an unclassified role — e.g. a new GL role added
    without updating the map) is dropped from RWA here. That is the same silent
    blind spot as an unweighted asset, so it is named in `unclassified_roles`
    rather than vanishing: a nonzero unclassified balance may be an asset the
    capital charge is missing.
    """
    rwa: dict[str, Decimal] = {}
    used: dict[str, Decimal] = {}
    assumed: list[str] = []
    unclassified: list[str] = []
    for role in set(snapshot) | set(risk.risk_weights):
        line = roles.STATEMENT_LINE.get(role)
        if line != "asset":
            if line is None and snapshot.get(role, Decimal(0)) != 0:
                unclassified.append(role)
            continue
        weight = risk.risk_weights.get(role)
        if weight is None:
            weight = risk.default_asset_weight
            assumed.append(role)
        used[role] = weight
        rwa[role] = (snapshot.get(role, Decimal(0)) * weight).quantize(Decimal("0.01"))
    total = sum(rwa.values(), Decimal(0))
    return {"rwa": rwa, "total_rwa": total,
            "risk_weights": used, "assumed_weight_roles": sorted(assumed),
            "unclassified_roles": sorted(unclassified),
            "economic_capital": (total * risk.target_ratio).quantize(Decimal("0.01"))}


def expected_loss(snapshot: dict, risk: RiskConfig) -> Decimal:
    total = Decimal(0)
    for role, rate in risk.loss_rates.items():
        total += snapshot.get(role, Decimal(0)) * rate
    return total


def credit_exposure(snapshot: dict, risk: RiskConfig) -> Decimal:
    """The balances expected loss is charged against — the denominator behind
    any 'expected loss is x% of the book' statement."""
    return sum((snapshot.get(role, Decimal(0)) for role in risk.loss_rates),
               Decimal(0))


def raroc(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict:
    inc = reports.income_statement(closing, opening)
    ni = inc["net_income"]
    # Annualise multiply-first (x * 365 / days) so exact figures stay exact.
    ni_ann = ni * Decimal(365) / Decimal(days)
    el = expected_loss(closing, risk)
    exposure = credit_exposure(closing, risk)
    ec = economic_capital(closing, risk)
    rar = ni_ann - el
    return {
        "net_income": ni,
        "net_income_annualized": ni_ann,
        "expected_loss": el,
        # The period-equivalent credit cost, precomputed. Without it a reader
        # wanting to compare credit cost against the period's net income has to
        # rescale by hand — and netting the *annual* expected loss against one
        # month of income turns a profitable month into a fake loss.
        "expected_loss_period": el * Decimal(days) / Decimal(365),
        "credit_exposure": exposure,
        "expected_loss_rate": _safe_div(el, exposure),
        "risk_adjusted_return": rar,
        "economic_capital": ec["economic_capital"],
        "total_rwa": ec["total_rwa"],
        "rwa": ec["rwa"],
        "risk_weights": ec["risk_weights"],
        "assumed_weight_roles": ec["assumed_weight_roles"],
        "period_days": days,
        # Periodicity travels with the figures: mixing an annual number into a
        # monthly comparison is the easiest way to misread this whole bundle.
        "units": {
            "net_income": f"CAD, {days}-day period",
            "net_income_annualized": "CAD, annual",
            "expected_loss": "CAD, annual",
            "expected_loss_period": f"CAD, {days}-day period",
            "credit_exposure": "CAD, point-in-time",
            "expected_loss_rate": "annual ratio of expected_loss to credit_exposure",
            "risk_adjusted_return": "CAD, annual",
            "economic_capital": "CAD, point-in-time",
            "total_rwa": "CAD, point-in-time",
            "rwa": "CAD, point-in-time, per role",
            "raroc": "annual ratio",
        },
        "raroc": _safe_div(rar, ec["economic_capital"]),
        # The GL has no loan-loss provision account, so credit cost is NOT in
        # net income; it enters here as expected loss. Consumers (the CFO agent
        # included) must not describe ROA/ROE as after-credit-loss measures.
        "basis": ("net income is pre-provision — the ledger books no loan-loss "
                  "provision, so expected credit loss is deducted here in RAROC "
                  "and is NOT reflected in net income, ROA or ROE. expected_loss "
                  "is an ANNUAL figure netted against net_income_annualized; to "
                  "state credit cost for this period use expected_loss_period, "
                  "never expected_loss"),
    }


def key_ratios(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict:
    bs = reports.balance_sheet(closing)
    inc = reports.income_statement(closing, opening)
    nim_out = reports.nim(closing, opening, days)
    ec = economic_capital(closing, risk)

    def ann(x: Decimal) -> Decimal:
        return x * Decimal(365) / Decimal(days)

    ni_ann = ann(inc["net_income"])

    ii = inc["income"].get("InterestIncome", Decimal(0))
    ie = inc["expense"].get("InterestExpense", Decimal(0))
    fee = inc["income"].get("FeeIncome", Decimal(0))
    interchange = inc["income"].get("InterchangeIncome", Decimal(0))
    opex = inc["expense"].get("OperatingExpense", Decimal(0))
    total_revenue = (ii - ie) + fee + interchange

    total_assets = bs["total_assets"]
    total_equity = sum(bs["equity"].values(), Decimal(0))
    capital_base = sum((v for k, v in bs["equity"].items()
                        if k != "CurrentEarnings"), Decimal(0))
    loans = sum((closing.get(r, Decimal(0)) for r in
                 ("CardReceivable", "OverdraftReceivable", "LoansReceivable")),
                Decimal(0))
    deposits_close = -closing.get("CustomerDeposits", Decimal(0))
    deposits_open = -opening.get("CustomerDeposits", Decimal(0))
    avg_deposits = (deposits_open + deposits_close) / Decimal(2)

    return {
        "roa": _safe_div(ni_ann, total_assets),
        "roe": _safe_div(ni_ann, capital_base),
        "efficiency_ratio": _safe_div(opex, total_revenue),
        "loan_to_deposit": _safe_div(loans, deposits_close),
        "leverage_ratio": _safe_div(total_equity, total_assets),
        "rwa_capital_ratio": _safe_div(total_equity, ec["total_rwa"]),
        "cost_of_funds": _safe_div(ann(ie), avg_deposits),
        "yield_on_earning_assets": _safe_div(ann(ii),
                                             nim_out["avg_earning_assets"]),
    }


def provision_scenario(closing: dict, opening: dict, days: int,
                       risk: RiskConfig, provision: Decimal) -> dict:
    """What booking a loan-loss provision would do to the headline returns.

    The ledger books no provision, so "what if we did" is a fair question — and
    it is where hand arithmetic goes wrong. Reported ROA/ROE are annualised; a
    hypothetical worked off the raw period income is ~12x too small and reads
    as directly comparable. Both sides are annualised here, and the capital
    base is the same one key_ratios uses (a provision lands in current
    earnings, which the capital base excludes, so the denominator holds).
    """
    bs = reports.balance_sheet(closing)
    ni = reports.income_statement(closing, opening)["net_income"]
    ni_after = ni - provision

    def ann(x: Decimal) -> Decimal:
        return x * Decimal(365) / Decimal(days)

    total_assets = bs["total_assets"]
    capital_base = sum((v for k, v in bs["equity"].items()
                        if k != "CurrentEarnings"), Decimal(0))
    return {
        "provision": provision,
        "net_income": ni,
        "net_income_after": ni_after,
        "total_assets": total_assets,
        # the provision sits as an allowance against the book
        "total_assets_after": total_assets - provision,
        "roa_before": _safe_div(ann(ni), total_assets),
        "roa_after": _safe_div(ann(ni_after), total_assets - provision),
        "roe_before": _safe_div(ann(ni), capital_base),
        "roe_after": _safe_div(ann(ni_after), capital_base),
        "period_days": days,
        "units": {
            "provision": f"CAD, charged in the {days}-day period",
            "net_income": f"CAD, {days}-day period",
            "net_income_after": f"CAD, {days}-day period",
            "roa_before": "annual ratio", "roa_after": "annual ratio",
            "roe_before": "annual ratio", "roe_after": "annual ratio",
        },
        "basis": ("hypothetical — no provision is booked in the ledger; this "
                  "restates the period's returns as if `provision` had been "
                  "charged, with both sides annualised the same way"),
    }


def financial_health(closing: dict, opening: dict, days: int,
                     risk: RiskConfig) -> dict:
    return {
        "balance_sheet": reports.balance_sheet(closing),
        "income_statement": reports.income_statement(closing, opening),
        "nim": reports.nim(closing, opening, days),
        "key_ratios": key_ratios(closing, opening, days, risk),
        "raroc": raroc(closing, opening, days, risk),
    }

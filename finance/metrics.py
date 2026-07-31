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


def _credit_book(snapshot: dict, risk: RiskConfig) -> dict:
    """Per-role credit exposure and expected loss, driven off the balance sheet.

    Same defect class as the original economic_capital bug: iterating the
    loss-rate table instead of the book made a credit-exposed role with no
    configured rate contribute zero to *both* expected loss and exposure — and
    because expected_loss_rate divides one by the other, the ratio stayed
    plausible. Here the credit-exposed roles come from `roles.CREDIT_EXPOSED_ROLES`
    (the book), and a role present but unconfigured falls back to
    `default_loss_rate` and is named in `assumed` rather than vanishing.
    """
    exposure: dict[str, Decimal] = {}
    loss: dict[str, Decimal] = {}
    assumed: list[str] = []
    for role in roles.CREDIT_EXPOSED_ROLES:
        bal = snapshot.get(role, Decimal(0))
        # Include a role if it carries a balance, or if it is configured (so a
        # configured-but-absent role still reads as a deliberate 0, matching the
        # old behaviour); skip an unconfigured role with no balance so we never
        # invent exposure that isn't on the book.
        if bal == 0 and role not in risk.loss_rates:
            continue
        rate = risk.loss_rates.get(role)
        if rate is None:
            rate = risk.default_loss_rate
            assumed.append(role)
        exposure[role] = bal
        loss[role] = bal * rate
    return {"exposure": exposure, "loss": loss, "assumed": sorted(assumed)}


def expected_loss(snapshot: dict, risk: RiskConfig) -> Decimal:
    return sum(_credit_book(snapshot, risk)["loss"].values(), Decimal(0))


def credit_exposure(snapshot: dict, risk: RiskConfig) -> Decimal:
    """The balances expected loss is charged against — the denominator behind
    any 'expected loss is x% of the book' statement."""
    return sum(_credit_book(snapshot, risk)["exposure"].values(), Decimal(0))


def raroc(closing: dict, opening: dict, days: int, risk: RiskConfig) -> dict:
    inc = reports.income_statement(closing, opening)
    ni = inc["net_income"]
    # Annualise multiply-first (x * 365 / days) so exact figures stay exact.
    ni_ann = ni * Decimal(365) / Decimal(days)
    cb = _credit_book(closing, risk)
    el = sum(cb["loss"].values(), Decimal(0))
    exposure = sum(cb["exposure"].values(), Decimal(0))
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
        # A credit-exposed role charged at the default loss rate rather than a
        # configured one — an assumption, not policy, same as assumed_weight_roles.
        "assumed_loss_roles": cb["assumed"],
        # A role economic_capital dropped from RWA because STATEMENT_LINE doesn't
        # classify it — a nonzero one may be an asset the capital charge is
        # missing. Propagated so financial_health surfaces it too; without this
        # the warning was computed and then discarded.
        "unclassified_roles": ec["unclassified_roles"],
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
        # Guard a negative denominator, not just zero: a loss-making period has
        # negative total_revenue, and opex / negative-revenue is a negative
        # "efficiency" that reads as an ordinary (good) ratio. Undefined is honest.
        "efficiency_ratio": (_safe_div(opex, total_revenue)
                             if total_revenue > 0 else None),
        "loan_to_deposit": _safe_div(loans, deposits_close),
        "leverage_ratio": _safe_div(total_equity, total_assets),
        "rwa_capital_ratio": _safe_div(total_equity, ec["total_rwa"]),
        "cost_of_funds": _safe_div(ann(ie), avg_deposits),
        "yield_on_earning_assets": _safe_div(ann(ii),
                                             nim_out["avg_earning_assets"]),
        # All ratios are annualised (returns/costs scaled to a year, stocks
        # point-in-time). Without this the most-quoted tool in the set carried no
        # periodicity label at all — the same unlabelled-periodicity trap raroc
        # fixed with its own units map.
        "units": {
            "roa": "annual ratio", "roe": "annual ratio",
            "efficiency_ratio": "ratio, period (opex / period revenue)",
            "loan_to_deposit": "ratio, point-in-time",
            "leverage_ratio": "ratio, point-in-time",
            "rwa_capital_ratio": "ratio, point-in-time",
            "cost_of_funds": "annual ratio", "yield_on_earning_assets": "annual ratio",
        },
        # ROE and the two capital-adequacy ratios divide by DIFFERENT capital
        # definitions on purpose; state it so an agent reporting them together
        # doesn't read one number against two silently-different denominators.
        "basis": ("roe divides by capital_base — equity EXCLUDING current "
                  "earnings, so a period's own profit doesn't flatter its return "
                  "on capital; leverage_ratio and rwa_capital_ratio divide by "
                  "total_equity, which INCLUDES current earnings, per the "
                  "regulatory capital convention. Do not compare roe against the "
                  "capital ratios as if they shared a denominator"),
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

from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings, RiskConfig
from .db import FinanceDB
from . import ledger_client, snapshots, reports, metrics


@dataclass
class Deps:
    db: FinanceDB
    nano_bank_api: str


def _month_range(period: str):
    """('YYYY-MM' or 'YYYY') -> (start_iso, end_iso, prior_period, days)."""
    import datetime as dt

    if "-" in period:
        y, m = (int(x) for x in period.split("-"))
        start = dt.date(y, m, 1)
        end = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
        prior = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    else:
        y = int(period)
        start = dt.date(y, 1, 1)
        end = dt.date(y + 1, 1, 1)
        prior = str(y - 1)
    days = (end - start).days
    return start.isoformat(), end.isoformat(), prior, days


_OPENING_MISSING_NOTE = (
    "no prior-period snapshot, so these are inception-to-date figures, NOT one "
    "period's flows — the first closed period legitimately has no prior; a later "
    "one missing its prior needs that period closed before the flows are real")


def _closed(deps: "Deps", period: str):
    """The snapshot for a period, or None if the period was never closed.

    read_snapshot returns {} for a missing period, and nothing downstream tells
    that apart from a genuine all-zero book — so income figures come out
    inception-to-date and a balance sheet reads all-zeros with balanced=True.
    Distinguishing the two here, at the tool boundary, is the layer the fix
    belongs at.
    """
    snap = deps.db.read_snapshot(period)
    return snap if snap else None


def _not_closed(deps: "Deps", period: str) -> dict:
    return {"error": "period not closed", "period": period,
            "available": deps.db.list_periods()}


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {("|".join(map(str, k)) if isinstance(k, tuple) else k): _stringify(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def build_mcp(deps: Deps) -> FastMCP:
    mcp = FastMCP("nano-finance",
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=False))

    @mcp.tool()
    def close_period(period: str) -> dict:
        """Capture/refresh the GL trial-balance snapshot for a period (YYYY-MM)."""
        rows = ledger_client.get_balances(deps.nano_bank_api)
        return snapshots.close_period(period, rows, deps.db)

    @mcp.tool()
    def list_periods() -> list:
        """Periods with a snapshot available."""
        return deps.db.list_periods()

    @mcp.tool()
    def balance_sheet(period: str) -> dict:
        """Balance Sheet as of a closed period."""
        snap = _closed(deps, period)
        if snap is None:
            return _not_closed(deps, period)
        return _stringify(reports.balance_sheet(snap))

    @mcp.tool()
    def income_statement(period: str) -> dict:
        """Income Statement for a period (needs this period + the prior close)."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, _ = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(reports.income_statement(closing, opening or {}))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    @mcp.tool()
    def nim(period: str) -> dict:
        """Net interest margin for a period."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, days = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(reports.nim(closing, opening or {}, days))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    @mcp.tool()
    def segment_pnl(period: str) -> dict:
        """P&L by product and cost-centre for a period."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        start, end, prior, _ = _month_range(period)
        opening = _closed(deps, prior)
        inc = reports.income_statement(closing, opening or {})
        interchange = inc["income"].get("InterchangeIncome", Decimal(0))
        # Hand the GL's net income in so the report reconciles itself: the
        # segments come from the operational tables and the income statement
        # from the GL snapshot, and the two can drift apart silently.
        return _stringify(reports.segment_pnl(
            deps.db.accruals(start, end), deps.db.fees(start, end), interchange,
            gl_net_income=inc["net_income"]))

    @mcp.tool()
    def raroc(period: str) -> dict:
        """Risk-adjusted return on capital (Basel-lite) for a period."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, days = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(metrics.raroc(closing, opening or {}, days,
                                       RiskConfig.from_env()))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    @mcp.tool()
    def provision_scenario(period: str, provision: str) -> dict:
        """Restate a period's ROA/ROE as if a loan-loss provision were charged.

        `provision` is a CAD amount. Use this for any "what if we provisioned
        X" question — never work it out by hand, the reported returns are
        annualised and a hand-rolled hypothetical will not be.
        """
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, days = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(metrics.provision_scenario(
            closing, opening or {}, days, RiskConfig.from_env(),
            Decimal(provision)))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    @mcp.tool()
    def key_ratios(period: str) -> dict:
        """Key CFO ratios (ROA/ROE/efficiency/LDR/leverage/CoF/yield) for a period."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, days = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(metrics.key_ratios(closing, opening or {}, days,
                                            RiskConfig.from_env()))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    @mcp.tool()
    def financial_health(period: str) -> dict:
        """Full financial-health bundle: balance sheet, income statement, NIM,
        key ratios and RAROC for a period."""
        closing = _closed(deps, period)
        if closing is None:
            return _not_closed(deps, period)
        _, _, prior, days = _month_range(period)
        opening = _closed(deps, prior)
        out = _stringify(metrics.financial_health(closing, opening or {}, days,
                                                  RiskConfig.from_env()))
        if opening is None:
            out["opening_snapshot_missing"] = True
            out["note"] = _OPENING_MISSING_NOTE
        return out

    return mcp


def build_deps(settings: Settings) -> Deps:
    db = FinanceDB(settings.db)
    db.ensure_schema()
    return Deps(db=db, nano_bank_api=settings.nano_bank_api)


def main():
    settings = Settings.from_env()
    mcp = build_mcp(build_deps(settings))
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()

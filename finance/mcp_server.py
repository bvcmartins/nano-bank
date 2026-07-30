from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .db import FinanceDB
from . import ledger_client, snapshots, reports


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
        return _stringify(reports.balance_sheet(deps.db.read_snapshot(period)))

    @mcp.tool()
    def income_statement(period: str) -> dict:
        """Income Statement for a period (needs this period + the prior close)."""
        _, _, prior, _ = _month_range(period)
        return _stringify(reports.income_statement(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior)))

    @mcp.tool()
    def nim(period: str) -> dict:
        """Net interest margin for a period."""
        _, _, prior, days = _month_range(period)
        return _stringify(reports.nim(
            deps.db.read_snapshot(period), deps.db.read_snapshot(prior), days))

    @mcp.tool()
    def segment_pnl(period: str) -> dict:
        """P&L by product and cost-centre for a period."""
        start, end, prior, _ = _month_range(period)
        inc = reports.income_statement(deps.db.read_snapshot(period),
                                       deps.db.read_snapshot(prior))
        interchange = inc["income"].get("InterchangeIncome", Decimal(0))
        return _stringify(reports.segment_pnl(
            deps.db.accruals(start, end), deps.db.fees(start, end), interchange))

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

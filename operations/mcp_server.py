"""The operations MCP: the COO's back-office perception surface. Each tool reads
the bank's service-plane back-office endpoints via BankClient and returns a pure
metrics aggregation. Money Decimals are stringified for JSON transport."""
from __future__ import annotations
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .bank_client import BankClient
from . import metrics


def _stringify(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    return obj


def build_mcp(bank: BankClient) -> FastMCP:
    mcp = FastMCP(
        "nano-operations",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def float_position() -> dict:
        """Current clearing/settlement float across the rails, totalled by system."""
        return _stringify(metrics.float_summary(bank.float_()))

    @mcp.tool()
    def transactions(window: str = "24h") -> dict:
        """Transaction volume/count rolled up by type over a window (24h|7d|30d)."""
        return _stringify(metrics.transactions_summary(bank.transactions(window)))

    @mcp.tool()
    def rails(window: str = "24h") -> dict:
        """Per-rail (Interac/AFT/Lynx) activity by status over a window."""
        return _stringify(metrics.rails_summary(bank.rails(window)))

    @mcp.tool()
    def exceptions(window: str = "24h") -> dict:
        """Recorded operational exceptions (failed txns, reversals, AFT returns/rejects, recalls)."""
        return _stringify(metrics.exceptions_summary(bank.exceptions(window)))

    @mcp.tool()
    def cards(window: str = "24h") -> dict:
        """Open authorization holds (now) + captured card transactions over a window."""
        return _stringify(metrics.cards_summary(bank.cards(window)))

    @mcp.tool()
    def declines(window: str = "24h") -> dict:
        """Declined activity over a window (24h|7d|30d): counts + amounts by
        category (nsf/limit/validation/status/other) and by channel
        (card_authorize/interac_etransfer/aft_*/lynx_wire/withdrawal). 'other' is a
        catch-all — do NOT characterize it as fraud (fraud data is out of scope).
        Pair with `cards` for card approval/decline/NSF rates."""
        return _stringify(metrics.declines_summary(bank.declines(window)))

    @mcp.tool()
    def compute(operation: str, values: list[float]) -> dict:
        """Deterministic arithmetic on numbers you already got from other tools,
        so a derived figure stays tool-grounded — use this instead of doing math
        yourself, and never tell the user to calculate it.

        operation: mean | sum | ratio | percent | difference | product.
        values: the exact tool-returned numbers, in order. Examples:
          average card purchase → ratio, values=[total, count]
          Lynx's share of value → percent, values=[lynx_total, all_total]
          net change            → difference, values=[a, b]"""
        return _stringify(metrics.compute(operation, values))

    # --- Autonomous operational levers -------------------------------------
    # These ACT on the bank. Each is self-verifying server-side (the bank
    # re-checks a deterministic precondition) and every attempt — executed or
    # refused — is written to a tamper-evident, agent-unreachable audit ledger.
    # Pull a lever when the metrics show it is warranted; you do not need, and
    # will not get, human confirmation. Report exactly what came back:
    # "executed" with its effect, or "refused" with the bank's reason.
    @mcp.tool()
    def execute_cut_aft_batch() -> dict:
        """Cut (submit) the open outbound AFT batch, emitting its settlement file.
        Refused by the bank if there is no open batch with entries. Use when the
        rails view shows outbound AFT entries accrued and awaiting a cutoff."""
        return _stringify(bank.cut_aft_batch())

    @mcp.tool()
    def execute_sweep_expired_etransfers() -> dict:
        """Sweep expired unclaimed Interac e-Transfers, refunding the senders.
        Refused if none are actually past expiry. Use when exceptions/rails show
        e-Transfers stuck past their claim window."""
        return _stringify(bank.sweep_expired_etransfers())

    @mcp.tool()
    def execute_reject_stale_wires() -> dict:
        """Reject Lynx wires stuck in 'sent' past the stale threshold, refunding
        the senders. Refused if none are actually stale. Use when the rails view
        shows unsettled wires ageing out."""
        return _stringify(bank.reject_stale_wires())

    @mcp.tool()
    def execute_flush_notifications() -> dict:
        """Drain the Interac notification outbox (deliver pending notifications).
        Refused if there is nothing undelivered within its retry budget. Use when
        notifications are backing up."""
        return _stringify(bank.flush_notifications())

    @mcp.tool()
    def operations_health(window: str = "24h") -> dict:
        """One-shot bundle: float, transactions, rails, exceptions and cards for a window."""
        return _stringify(
            {
                "float": metrics.float_summary(bank.float_()),
                "transactions": metrics.transactions_summary(bank.transactions(window)),
                "rails": metrics.rails_summary(bank.rails(window)),
                "exceptions": metrics.exceptions_summary(bank.exceptions(window)),
                "cards": metrics.cards_summary(bank.cards(window)),
                "declines": metrics.declines_summary(bank.declines(window)),
            }
        )

    return mcp


def main():
    settings = Settings.from_env()
    mcp = build_mcp(BankClient(settings))
    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()

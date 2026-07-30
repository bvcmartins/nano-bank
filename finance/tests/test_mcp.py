import asyncio
from finance import mcp_server


class FakeDeps:
    class db:
        @staticmethod
        def list_periods():
            return ["2026-06", "2026-07"]

    nano_bank_api = "http://localhost:8081"


def test_tools_registered():
    mcp = mcp_server.build_mcp(FakeDeps())
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"close_period", "list_periods", "balance_sheet",
            "income_statement", "nim", "segment_pnl"} <= names

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


def test_new_metric_tools_registered():
    mcp = mcp_server.build_mcp(FakeDeps())
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"raroc", "key_ratios", "financial_health"} <= names


class _DB:
    def __init__(self, snapshots):
        self._s = snapshots

    def read_snapshot(self, period):
        return dict(self._s.get(period, {}))

    def list_periods(self):
        return sorted(self._s)


class _Deps:
    def __init__(self, snapshots):
        self.db = _DB(snapshots)
        self.nano_bank_api = "http://localhost:8081"


def test_missing_period_is_reported_not_silently_zeroed():
    """read_snapshot returns {} for a period that was never closed, and nothing
    downstream told that apart from a genuine all-zero book — a balance sheet
    came back all zeros with balanced=True. The tool boundary now distinguishes
    the two."""
    deps = _Deps({"2026-07": {"Bank": None}})
    assert mcp_server._closed(deps, "2026-07") is not None
    assert mcp_server._closed(deps, "2026-08") is None
    err = mcp_server._not_closed(deps, "2026-08")
    assert err["error"] == "period not closed"
    assert err["available"] == ["2026-07"]
